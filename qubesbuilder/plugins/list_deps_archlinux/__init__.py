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
from qubesbuilder.plugins import (
    ArchlinuxDistributionPlugin,
    PluginContext,
    PluginDependency,
)
from qubesbuilder.plugins.list_deps import ListDepsPlugin


class ArchLinuxListDepsPlugin(ArchlinuxDistributionPlugin, ListDepsPlugin):

    context = PluginContext.COMPONENT | PluginContext.DIST
    name = "list_deps_archlinux"

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
        self.dependencies.append(PluginDependency("source_archlinux"))

    def build_command(
        self, build, source_dir: Path, out_path: Path
    ) -> List[str]:
        pkgbuild_in = f"{source_dir}/{build}/PKGBUILD.in"
        pkgbuild = f"{source_dir}/{build}/PKGBUILD"
        # Replace @VERSION@/@REL@ placeholders so bash can source the file.
        return [
            f"if [ -e {pkgbuild_in} ]; then "
            f"sed -e 's|@VERSION@|0|g' -e 's|@REL@|1|g' "
            f"-e 's|@BACKEND_VMM@|xen|g' {pkgbuild_in} > {pkgbuild}; "
            f"fi",
            f"bash -c 'source {pkgbuild} && "
            f'printf "%s\\n" "${{makedepends[@]}}" "${{checkdepends[@]}}"\' '
            f"| sed '/^$/d' | sort -u > {out_path}",
        ]


PLUGINS = [ArchLinuxListDepsPlugin]
