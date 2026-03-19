# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2026 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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
from qubesbuilder.plugins import WindowsDistributionPlugin
from qubesbuilder.plugins.chroot import ChrootPlugin


class WindowsChrootPlugin(WindowsDistributionPlugin, ChrootPlugin):
    """
    WindowsChrootPlugin - dummy init-cache stage for Windows distributions.

    Stages:
        - init-cache
    """

    name = "chroot_windows"
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
        chroot_dir = self.config.cache_dir / "chroot" / self.dist.distribution
        artifact_dir = chroot_dir / self.dist.nva
        artifact_dir.mkdir(parents=True, exist_ok=True)

        if not force and self.get_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            artifacts_dir=artifact_dir,
        ):
            self.log.info(
                f"{self.dist}: Re-using existing cache. Use --force to recreate."
            )
            return

        self.save_artifacts_info(
            stage=self.stage,
            basename=self.dist.nva,
            info={"packages": []},
            artifacts_dir=artifact_dir,
        )
        self.log.info(f"{self.dist}: Windows init-cache created (no-op).")


PLUGINS = [WindowsChrootPlugin]
