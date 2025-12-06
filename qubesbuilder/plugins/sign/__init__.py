# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.plugins import (
    DistributionComponentPlugin,
    PluginError,
    JobDependency,
    JobReference,
)


class SignError(PluginError):
    pass


class SignPlugin(DistributionComponentPlugin):
    """
    SignPlugin manages generic distribution sign.

    Stages:
        - sign - Ensure all build targets artifacts exist from previous required stages.

    Entry points:
        - build
    """

    name = "sign"
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
        )

        if self.has_component_packages(stage="build"):
            for build in self.get_parameters(stage="build").get("build", []):
                self.dependencies.append(
                    JobDependency(
                        JobReference(
                            component=self.component,
                            dist=self.dist,
                            stage="build",
                            build=build.mangle(),
                            template=None,
                        )
                    )
                )

    @classmethod
    def from_args(cls, **kwargs):
        component = kwargs.get("component")
        config = kwargs.get("config")
        dist = kwargs.get("dist")
        stage = kwargs.get("stage")
        if stage != "sign":
            return None
        if component and not component.has_packages:
            return None
        if not cls.supported_distribution(dist):
            return None
        if not cls.is_signing_configured(config, dist, component):
            return None
        return super().from_args(**kwargs)

    def run(self, **kwargs):
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("sign"):
            return

        if not isinstance(self.executor, LocalExecutor):
            raise SignError("This plugin only supports local executor.")
