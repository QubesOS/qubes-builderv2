# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2023 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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

from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import ArchlinuxDistributionPlugin
from qubesbuilder.plugins.chroot import ChrootError, ChrootPlugin


def get_pacman_cmd(
    gen_path,
    conf_template,
    conf,
    servers=None,
    enable_builder_local=False,
    use_qubes_repo_version=None,
    use_qubes_repo_testing=False,
):
    # Prepare generate-pacman args
    gen_pacman_cmd = [
        "sudo",
        gen_path,
        conf_template,
        conf,
    ]
    for server in servers or []:
        gen_pacman_cmd += ["--server", server.rstrip("/")]

    # Host (cage) has access to builder-local repository that will be mounted bind into
    # the archlinux chroot. Configurations will be copied-in by qubes-x86_64-build (archbuild).
    if enable_builder_local:
        gen_pacman_cmd.append("--enable-builder-local")

    if use_qubes_repo_version:
        gen_pacman_cmd += [
            "--use-qubes-repo-version",
            str(use_qubes_repo_version),
        ]
        if use_qubes_repo_testing:
            gen_pacman_cmd.append("--use-qubes-repo-testing")

    return [" ".join(gen_pacman_cmd)]


def get_archchroot_cmd(
    chroot_dir, pacman_conf, makepkg_conf, additional_packages=None
):
    additional_packages = additional_packages or []

    mkarchchroot_cmd = [
        "sudo",
        "mkarchroot",
        "-C",
        str(pacman_conf),
        "-M",
        str(makepkg_conf),
        str(chroot_dir),
        "base-devel",
    ] + additional_packages

    cmd = [
        "sudo rm -rf /etc/pacman.d/gnupg/private-keys-v1.d",
        "sudo pacman-key --init",
        "sudo pacman-key --populate",
        f"sudo mkdir -p {chroot_dir.parent}",
        " ".join(mkarchchroot_cmd),
    ]

    return cmd


class ArchlinuxChrootPlugin(ArchlinuxDistributionPlugin, ChrootPlugin):
    """
    ArchlinuxChrootPlugin manages Archlinux chroot creation

    Stages:
        - chroot - Create Archlinux cache chroot.
    """

    name = "chroot_archlinux"
    stages = ["init-cache"]

    def __init__(
        self,
        dist: QubesDistribution,
        config: Config,
        stage: str,
        **kwargs,
    ):
        super().__init__(dist=dist, config=config, stage=stage, **kwargs)

    def run(self, force: bool = False, **kwargs):
        """
        Run plugin for given stage.
        """

        chroot_dir = self.config.cache_dir / "chroot" / self.dist.distribution
        chroot_archive = f"root.tar.gz"

        artifacts_info = self.get_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            artifacts_dir=chroot_dir / self.dist.nva,
        )

        existing_packages = artifacts_info.get("packages", [])

        additional_packages = (
            self.config.get("cache", {})
            .get(self.dist.distribution, {})
            .get("packages", [])
        )

        # Delete previous chroot if forced or package sets differ
        if artifacts_info:
            if force:
                msg = f"{self.dist}: Forcing cache recreation..."
                recreate = True
            elif set(additional_packages) != set(existing_packages):
                msg = (
                    f"{self.dist}: Existing packages in cache differ from requested ones. "
                    f"Recreating cache..."
                )
                recreate = True
            else:
                msg = (
                    f"{self.dist}: Re-using existing cache. "
                    f"Use --force to force cleanup and recreation."
                )
                recreate = False

            self.log.info(msg)

            if not recreate:
                return

            (chroot_dir / self.dist.nva / chroot_archive).unlink()

        # Create chroot cache dir
        (chroot_dir / self.dist.nva).mkdir(exist_ok=True, parents=True)

        copy_in = self.default_copy_in(
            self.executor.get_plugins_dir(), self.executor.get_sources_dir()
        )
        self.environment.update(
            {
                "DIST": self.dist.name,
                "PACKAGE_SET": self.dist.package_set,
            }
        )
        copy_out = [
            (
                self.executor.get_cache_dir() / chroot_archive,
                chroot_dir / self.dist.nva,
            )
        ]

        pacman_conf_template = f"{self.executor.get_plugins_dir()}/chroot_archlinux/conf/pacman.conf.j2"

        pacman_conf = (
            "/usr/local/share/devtools/pacman.conf.d/qubes-x86_64.conf"
        )

        makepkg_conf = f"{self.executor.get_plugins_dir()}/chroot_archlinux/conf/makepkg-x86_64.conf"

        servers = self.config.get("mirrors", {}).get(
            self.dist.distribution, []
        ) or self.config.get("mirrors", {}).get(self.dist.name, [])

        pacman_cmd = get_pacman_cmd(
            gen_path=f"{self.executor.get_plugins_dir()}/chroot_archlinux/scripts/generate-pacman",
            conf_template=pacman_conf_template,
            conf=pacman_conf,
            servers=servers,
        )

        executor_chroot_dir = self.executor.get_cache_dir() / "root"

        cmd = pacman_cmd + get_archchroot_cmd(
            executor_chroot_dir,
            pacman_conf,
            makepkg_conf,
            additional_packages=additional_packages,
        )

        cmd += [
            f"cd {self.executor.get_cache_dir()}",
            f"sudo tar cf {chroot_archive} root",
        ]

        try:
            self.executor.run(
                cmd,
                copy_in,
                copy_out,
                environment=self.environment,
            )
        except ExecutorError as e:
            msg = f"{self.dist}: Failed to generate chroot: {str(e)}."
            raise ChrootError(msg) from e

        # Save packages info into artifacts file
        info = {
            "packages": additional_packages,
        }
        self.save_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            info=info,
            artifacts_dir=chroot_dir / self.dist.nva,
        )


PLUGINS = [ArchlinuxChrootPlugin]
