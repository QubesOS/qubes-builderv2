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

import shutil
import tempfile
from pathlib import Path
from typing import List

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import (
    DistributionComponentPlugin,
    PluginDependency,
    PluginError,
)


class ListDepsError(PluginError):
    pass


class ListDepsPlugin(DistributionComponentPlugin):
    """Base plugin for the list-deps stage."""

    name = "list_deps"
    stages = ["list-deps"]

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
        self.dependencies.append(PluginDependency("source"))
        self.environment.update({"DIST": self.dist.name, "LC_ALL": "C"})

    def has_component_packages(self, stage: str):
        # Reuse the prep stage check for list-deps.
        if stage == "list-deps":
            stage = "prep"
        return super().has_component_packages(stage)

    def build_command(
        self, build, source_dir: Path, out_path: Path
    ) -> List[str]:
        raise NotImplementedError

    def run(self, force_list_deps: bool = False, **kwargs):
        super().run()

        if not self.has_component_packages("list-deps"):
            return

        parameters = self.get_parameters("prep")
        if not parameters.get("build", []):
            self.log.info(f"{self.component}:{self.dist}: Nothing to be done.")
            return

        # Skip if all source hashes match and no forced re-run.
        if not force_list_deps and all(
            self.component.get_source_hash()
            == self.get_dist_artifacts_info(self.stage, build.mangle()).get(
                "source-hash", None
            )
            for build in parameters["build"]
        ):
            self.log.info(
                f"{self.component}:{self.dist}: Source hash unchanged. "
                f"Skipping."
            )
            return

        artifacts_dir = self.get_dist_component_artifacts_dir(self.stage)
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        for build in parameters["build"]:
            temp_dir = Path(tempfile.mkdtemp())
            source_dir = self.executor.get_builder_dir() / self.component.name
            build_bn = build.mangle()
            out_name = f"{build_bn}_build_deps.list"

            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(),
                self.executor.get_sources_dir(),
            ) + [
                (self.component.source_dir, self.executor.get_builder_dir()),
            ]
            copy_out = [(source_dir / out_name, temp_dir)]

            cmd = self.build_command(build, source_dir, source_dir / out_name)

            try:
                self.executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = (
                    f"{self.component}:{self.dist}:{build}: "
                    f"Failed to extract build dependencies: {str(e)}."
                )
                raise ListDepsError(msg) from e

            deps = []
            with open(temp_dir / out_name) as f:
                for line in f.read().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        deps.append(line)

            self.save_dist_artifacts_info(
                stage=self.stage,
                basename=build_bn,
                info={
                    "build": str(build),
                    "build-deps": deps,
                    "source-hash": self.component.get_source_hash(),
                },
            )
            shutil.rmtree(temp_dir)
