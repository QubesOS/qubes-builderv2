# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
import shutil

from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.plugins.chroot import ChrootError, ChrootPlugin


class RPMChrootPlugin(ChrootPlugin):
    dist_filter = staticmethod(lambda d: d.is_rpm())
    """
    ChrootPlugin manages RPM chroot creation

    Stages:
        - init-cache - Create Mock cache chroot.
    """

    name = "chroot_rpm"
    stages = ["init-cache"]

    def __init__(
        self,
        dist: QubesDistribution,
        config: Config,
        stage: str,
        **kwargs,
    ):
        super().__init__(dist=dist, config=config, stage=stage, **kwargs)

    def run(self, force=False, **kwargs):
        """
        Run plugin for given stage.
        """

        mock_conf = f"{self.dist.nva}.cfg"

        chroot_dir = self.config.cache_dir / "chroot" / self.dist.distribution

        artifacts_info = self.get_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            artifacts_dir=chroot_dir / self.dist.nva,
        )

        existing_packages = artifacts_info.get("packages", [])

        cache_dist_conf = self.config.get("cache", {}).get(
            self.dist.distribution, {}
        )
        additional_packages = cache_dist_conf.get("packages", [])
        # True: install into the chroot; False: download to dnf_cache only.
        install_into_chroot = bool(
            cache_dist_conf.get("install-packages", False)
        )
        existing_install_into_chroot = bool(
            artifacts_info.get("install-packages", False)
        )

        # Delete previous chroot if forced to do it or if packages set differs
        if artifacts_info:
            if force:
                msg = f"{self.dist}: Forcing cache recreation..."
                recreate = True
            elif set(additional_packages) != set(existing_packages):
                msg = f"{self.dist}: Existing packages in cache differs from requested ones. Recreating cache..."
                recreate = True
            elif install_into_chroot != existing_install_into_chroot:
                msg = f"{self.dist}: install-packages flag toggled; recreating cache..."
                recreate = True
            else:
                msg = f"{self.dist}: Re-using existing cache. Use --force to force cleanup and recreation."
                recreate = False

            self.log.info(msg)

            if not recreate:
                return

            shutil.rmtree(chroot_dir / self.dist.nva)

        # Create chroot cache dir
        chroot_dir.mkdir(exist_ok=True, parents=True)

        self.environment.update(
            {
                "DIST": self.dist.name,
                "PACKAGE_SET": self.dist.package_set,
            }
        )

        files_inside_executor_with_placeholders = [
            f"@PLUGINS_DIR@/chroot_rpm/mock/{mock_conf}"
        ]

        mock_cmd = [
            f"sudo --preserve-env=DIST,PACKAGE_SET,USE_QUBES_REPO_VERSION",
            f"/usr/libexec/mock/mock",
            f"--root {self.executor.get_plugins_dir()}/chroot_rpm/mock/{mock_conf}",
            "--disablerepo=builder-local",
        ]
        if isinstance(self.executor, ContainerExecutor):
            msg = (
                f"{self.dist}: Mock isolation set to 'simple', build has full network "
                f"access. Use 'qubes' executor for network-isolated build."
            )
            self.log.warning(msg)
            mock_cmd.append("--isolation=simple")
        else:
            mock_cmd.append("--isolation=nspawn")
        if self.config.verbose:
            mock_cmd.append("--verbose")

        # Create a first cage to generate the mock chroot
        copy_in = self.default_copy_in(
            self.executor.get_plugins_dir(), self.executor.get_sources_dir()
        )
        copy_out = [
            (
                self.executor.get_cache_dir() / f"mock/{self.dist.nva}",
                chroot_dir,
            )
        ]
        cmd = [" ".join(mock_cmd + ["--init"])]
        try:
            self.executor.run(
                cmd,
                copy_in,
                copy_out,
                environment=self.environment,
                files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
            )
        except ExecutorError as e:
            msg = f"{self.dist}: Failed to generate chroot: {str(e)}."
            raise ChrootError(msg) from e

        # Create a second cage for downloading the packages
        if additional_packages:
            # Remove dnf_cache
            if (chroot_dir / self.dist.nva / "dnf_cache").exists():
                shutil.rmtree(chroot_dir / self.dist.nva / "dnf_cache")
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (
                    chroot_dir / self.dist.nva,
                    self.executor.get_cache_dir() / f"mock",
                ),
            ]
            if install_into_chroot:
                # Persist root + dnf_cache so future --no-clean builds start with packages installed.
                copy_out = [
                    (
                        self.executor.get_cache_dir() / f"mock/{self.dist.nva}",
                        chroot_dir,
                    )
                ]
            else:
                copy_out = [
                    (
                        self.executor.get_cache_dir()
                        / f"mock/{self.dist.nva}/dnf_cache",
                        chroot_dir / self.dist.nva,
                    )
                ]
            for package in additional_packages:
                mock_cmd += ["--install", f"'{package}'"]
            cmd.append(" ".join(mock_cmd))
            if install_into_chroot:
                # mock's root_cache snapshot is taken at --init time and not
                # updated by --install. Re-tar the live root into cache.tar.gz
                # so future builds restore the post-install state.
                # Entries must be rooted at "./" to match mock's extract path.
                build_root = f"/builder/build/{self.dist.nva}/root"
                cache_root_cache = (
                    self.executor.get_cache_dir()
                    / f"mock/{self.dist.nva}/root_cache"
                )
                cmd.append(
                    f"sudo rm -f {cache_root_cache}/cache.tar.gz "
                    f"{cache_root_cache}/cache.tar"
                )
                cmd.append(
                    f"sudo tar -C {build_root} -czf "
                    f"{cache_root_cache}/cache.tar.gz ."
                )
                # chown so docker cp back to host does not fail on root-owned files.
                cmd.append(
                    f"sudo chown {self.executor.get_user()}:{self.executor.get_group()} "
                    f"{cache_root_cache}/cache.tar.gz"
                )
            try:
                self.executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                    files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
                )
            except ExecutorError as e:
                msg = (
                    f"{self.dist}: Failed to download extra packages: {str(e)}."
                )
                raise ChrootError(msg) from e

        # Save packages info into artifacts file
        info = {
            "packages": additional_packages,
            "install-packages": install_into_chroot,
        }
        self.save_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            info=info,
            artifacts_dir=chroot_dir / self.dist.nva,
        )


PLUGINS = [RPMChrootPlugin]
