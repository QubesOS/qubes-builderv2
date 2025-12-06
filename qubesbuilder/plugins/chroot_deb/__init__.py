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
import os
import shutil

from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import DEBDistributionPlugin
from qubesbuilder.plugins.chroot import ChrootPlugin, ChrootError


class DEBChrootPlugin(DEBDistributionPlugin, ChrootPlugin):
    """
    ChrootPlugin manages Debian chroot creation

    Stages:
        - chroot - Create pbuilder base.tgz.
    """

    name = "chroot_deb"
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

        pbuilder_dir = chroot_dir / self.dist.nva / "pbuilder"

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

            shutil.rmtree(pbuilder_dir)

        # Create chroot cache dir
        pbuilder_dir.mkdir(exist_ok=True, parents=True)

        files_inside_executor_with_placeholders = [
            "@PLUGINS_DIR@/chroot_deb/pbuilder/pbuilderrc"
        ]

        # Create a first cage to generate the base.tgz
        copy_in = self.default_copy_in(
            self.executor.get_plugins_dir(), self.executor.get_sources_dir()
        )
        copy_out = [
            (
                self.executor.get_builder_dir() / "pbuilder/base.tgz",
                pbuilder_dir,
            )
        ]
        cmd = [
            f"sed -i '/qubes-deb/d' {self.executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
            f"mkdir -p {self.executor.get_cache_dir()}/aptcache",
        ]
        # If provided, use the first mirror given in builder configuration mirrors list
        mirrors = self.config.get("mirrors", {}).get(
            self.dist.distribution, []
        ) or self.config.get("mirrors", {}).get(self.dist.fullname, [])
        if mirrors:
            cmd += [
                f"sed -i 's@MIRRORSITE=https://deb.debian.org/debian@MIRRORSITE={mirrors[0]}@' {self.executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc"
            ]
        pbuilder_cmd = [
            f"sudo -E pbuilder create --distribution {self.dist.name}",
            f"--configfile {self.executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
        ]
        cmd.append(" ".join(pbuilder_cmd))
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
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (
                    pbuilder_dir / "base.tgz",
                    self.executor.get_builder_dir() / "pbuilder",
                )
            ]
            copy_out = [
                (
                    self.executor.get_cache_dir() / "aptcache",
                    pbuilder_dir,
                )
            ]
            cmd = [
                f"sed -i '/qubes-deb/d' {self.executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
                f"mkdir -p {self.executor.get_cache_dir()}/aptcache",
            ]
            pbuilder_cmd = [
                f"sudo -E pbuilder execute --distribution {self.dist.name}",
                f"--configfile {self.executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
                f"--bindmounts {self.executor.get_cache_dir()}/aptcache:/tmp/aptcache",
                f"-- {self.executor.get_plugins_dir()}/chroot_deb/scripts/apt-download-packages {' '.join(additional_packages)}",
            ]
            cmd.append(" ".join(pbuilder_cmd))
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
        }
        self.save_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            info=info,
            artifacts_dir=chroot_dir / self.dist.nva,
        )


PLUGINS = [DEBChrootPlugin]
