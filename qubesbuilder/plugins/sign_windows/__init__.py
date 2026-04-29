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

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.plugins import PluginDependency
from qubesbuilder.plugins.sign import SignPlugin


class WindowsSignPlugin(SignPlugin):
    """
    WindowsSignPlugin - dummy sign stage for Windows distributions.

    Stages:
        - sign
    """

    dist_filter = staticmethod(lambda d: d.is_windows())

    name = "sign_windows"
    stages = ["sign"]

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config: Config,
        stage: str,
        **kwargs,
    ):
        super().__init__(
            component=component,
            dist=dist,
            config=config,
            stage=stage,
            **kwargs,
        )
        self.dependencies.append(PluginDependency("sign"))

    @classmethod
    def matches(cls, **kwargs) -> bool:
        # Windows signing uses Authenticode in the build stage, not GPG.
        # Skip the GPG key check from SignPlugin.matches().
        component = kwargs.get("component")
        if component and not component.has_packages:
            return False
        return super(SignPlugin, cls).matches(**kwargs)

    @classmethod
    def from_args(cls, **kwargs):
        if cls.matches(**kwargs):
            return cls(
                component=kwargs["component"],
                dist=kwargs["dist"],
                config=kwargs["config"],
                stage=kwargs["stage"],
            )
        return None

    def run(self, **kwargs):
        # Signing for Windows is done in the build stage.
        # Create dummy sign artifacts so downstream stages can track dependencies.
        for build in self.get_parameters("build").get("build", []):
            if not self.get_dist_artifacts_info(
                stage=self.stage, basename=build.mangle()
            ):
                self.save_dist_artifacts_info(
                    stage=self.stage,
                    basename=build.mangle(),
                    info={},
                )
        self.log.info(
            f"{self.component}:{self.dist}: Windows signing is handled by build_windows. Skipping sign stage."
        )


PLUGINS = [WindowsSignPlugin]
