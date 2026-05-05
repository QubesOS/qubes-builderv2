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

from pathlib import Path
from typing import List

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.plugins import PluginDependency
from qubesbuilder.plugins.list_deps import ListDepsPlugin


class RPMListDepsPlugin(ListDepsPlugin):
    dist_filter = staticmethod(lambda d: d.is_rpm())

    name = "list_deps_rpm"

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
        self.dependencies.append(PluginDependency("source_rpm"))
        self.environment.update({"PACKAGE_SET": self.dist.package_set})

    def build_command(
        self, build, source_dir: Path, out_path: Path
    ) -> List[str]:
        if self.config.increment_devel_versions:
            dist_tag = f"{self.component.devel}.{self.dist.tag}"
        else:
            dist_tag = self.dist.tag
        plugins_dir = self.executor.get_plugins_dir()
        return [
            f"{plugins_dir}/source_rpm/scripts/generate-spec "
            f"{source_dir} {source_dir / build}.in {source_dir / build} || true",
            f"{plugins_dir}/list_deps_rpm/scripts/get-build-deps "
            f"{source_dir / build} {dist_tag} > {out_path}",
        ]


PLUGINS = [RPMListDepsPlugin]
