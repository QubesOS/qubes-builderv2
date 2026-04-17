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
    Plugin,
    PluginContext,
    PluginError,
    JobDependency,
    JobReference,
)


class SignError(PluginError):
    pass


class SignPlugin(Plugin):
    context = PluginContext.COMPONENT | PluginContext.DIST
    component: QubesComponent
    dist: QubesDistribution
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

        # Always depend on the build job for this component/dist.
        self.dependencies.append(
            JobDependency(
                JobReference(
                    component=self.component,
                    dist=self.dist,
                    template=None,
                    stage="build",
                    build=None,
                )
            )
        )

    @classmethod
    def matches(cls, **kwargs) -> bool:
        if not super().matches(**kwargs):
            return False
        component = kwargs.get("component")
        if component and not component.has_packages:
            return False
        config = kwargs.get("config")
        dist = kwargs.get("dist")
        if not cls.is_signing_configured(config, dist, component):
            return False
        return True

    def run(self, **kwargs):
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("sign"):
            return

        if not isinstance(self.executor, LocalExecutor):
            raise SignError("This plugin only supports local executor.")
