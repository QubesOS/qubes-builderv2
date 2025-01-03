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
from qubesbuilder.pluginmanager import PluginManager
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
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(dist=dist, config=config, manager=manager)

    def run(self, stage: str):
        """
        Run plugin for given stage.
        """

        if stage != "init-cache":
            return

        executor = self.get_executor_from_config(stage)

        chroot_dir = (
            self.config.cache_dir / "chroot" / self.dist.name / "pbuilder"
        )
        chroot_dir.mkdir(exist_ok=True, parents=True)

        if (chroot_dir / "base.tgz").exists():
            os.remove(chroot_dir / "base.tgz")
        if (chroot_dir / "aptcache").exists():
            shutil.rmtree(chroot_dir / "aptcache")

        files_inside_executor_with_placeholders = [
            "@PLUGINS_DIR@/chroot_deb/pbuilder/pbuilderrc"
        ]

        # Create a first cage to generate the base.tgz
        copy_in = self.default_copy_in(
            executor.get_plugins_dir(), executor.get_sources_dir()
        )
        copy_out = [
            (
                executor.get_builder_dir() / "pbuilder/base.tgz",
                chroot_dir,
            )
        ]
        cmd = [
            f"sed -i '/qubes-deb/d' {executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
            f"mkdir -p {executor.get_cache_dir()}/aptcache",
        ]
        # If provided, use the first mirror given in builder configuration mirrors list
        mirrors = self.config.get("mirrors", {}).get(
            self.dist.distribution, []
        ) or self.config.get("mirrors", {}).get(self.dist.fullname, [])
        if mirrors:
            cmd += [
                f"sed -i 's@MIRRORSITE=https://deb.debian.org/debian@MIRRORSITE={mirrors[0]}@' {executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc"
            ]
        pbuilder_cmd = [
            f"sudo -E pbuilder create --distribution {self.dist.name}",
            f"--configfile {executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
        ]
        cmd.append(" ".join(pbuilder_cmd))
        try:
            executor.run(
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
        additional_packages = (
            self.config.get("cache", {})
            .get(self.dist.distribution, {})
            .get("packages", [])
        )
        if additional_packages:
            copy_in = self.default_copy_in(
                executor.get_plugins_dir(), executor.get_sources_dir()
            ) + [
                (
                    chroot_dir / "base.tgz",
                    executor.get_builder_dir() / "pbuilder",
                )
            ]
            copy_out = [
                (
                    executor.get_cache_dir() / "aptcache",
                    chroot_dir,
                )
            ]
            cmd = [
                f"sed -i '/qubes-deb/d' {executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
                f"mkdir -p {executor.get_cache_dir()}/aptcache",
            ]
            pbuilder_cmd = [
                f"sudo -E pbuilder execute --distribution {self.dist.name}",
                f"--configfile {executor.get_plugins_dir()}/chroot_deb/pbuilder/pbuilderrc",
                f"--bindmounts {executor.get_cache_dir()}/aptcache:/tmp/aptcache",
                f"-- {executor.get_plugins_dir()}/chroot_deb/scripts/apt-download-packages {' '.join(additional_packages)}",
            ]
            cmd.append(" ".join(pbuilder_cmd))
            try:
                executor.run(
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


PLUGINS = [DEBChrootPlugin]
