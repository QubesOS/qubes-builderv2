# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
# Copyright (C) 2024 Rafał Wojdyła <omeg@invisiblethingslab.com>
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
import tempfile
from pathlib import Path

from qubesbuilder.common import is_filename_valid
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.plugins import WindowsDistributionPlugin, PluginDependency
from qubesbuilder.plugins.source import SourcePlugin, SourceError


class WindowsSourcePlugin(WindowsDistributionPlugin, SourcePlugin):
    """
    Manage Windows distribution source.
    """

    name = "source_windows"
    stages = ["prep"]
    dependencies = [PluginDependency("fetch"), PluginDependency("source")]

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config: Config,
        stage: str,
        **kwargs,
    ):
        super().__init__(
            component=component, dist=dist, config=config, stage=stage
        )

    def run(self, **kwargs):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run()

        if self.stage != "prep" or not self.has_component_packages("prep"):
            return

        parameters = self.get_parameters(self.stage)

        # Check if we have distribution related content defined
        if not parameters.get("build", []):
            self.log.info(f"{self.component}:{self.dist}: Nothing to be done.")
            return

        # Compare previous artifacts hash with current source hash
        hash = self.get_dist_artifacts_info(
            self.stage, self.component.name
        ).get("source-hash", None)
        if self.component.get_source_hash() == hash:
            self.log.info(
                f"{self.component}:{self.dist}: Source hash is the same as already prepared source. Skipping."
            )
            return

        # Get fetch info
        fetch_info = self.get_dist_artifacts_info(
            "fetch",
            "source",
            artifacts_dir=self.get_component_artifacts_dir("fetch"),
        )

        artifacts_dir = self.get_dist_component_artifacts_dir(self.stage)

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        # Save package information we parsed for next stages
        info = fetch_info
        info.update(
            {
                "source-hash": self.component.get_source_hash(),
            }
        )

        self.save_dist_artifacts_info(
            stage=self.stage, basename=self.component.name, info=info
        )

        # TODO: create source archive if needed

        # save dummy per-target info, unused but base build plugin requires it
        for target in parameters["build"]:
            # this dummy info can't be an empty dict
            self.save_dist_artifacts_info(
                stage=self.stage, basename=target.mangle(), info={"dummy": 1}
            )


PLUGINS = [WindowsSourcePlugin]
