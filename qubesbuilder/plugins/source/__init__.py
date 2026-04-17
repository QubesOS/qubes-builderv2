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
from qubesbuilder.exc import ComponentError
from qubesbuilder.plugins import (
    Plugin,
    PluginContext,
    PluginError,
    PluginDependency,
    JobDependency,
    JobReference,
)


class SourceError(PluginError):
    pass


class SourcePlugin(Plugin):
    context = PluginContext.COMPONENT | PluginContext.DIST
    component: QubesComponent
    dist: QubesDistribution
    """
    SourcePlugin manage generic distribution source

    Stages:
        - prep: Check if 'fetch' artifacts info have been created.

    Entry points:
        - source
    """

    name = "source"

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config: Config,
        stage: str,
    ):
        super().__init__(
            component=component,
            dist=dist,
            config=config,
            stage=stage,
        )

        self.dependencies.append(PluginDependency("fetch"))

        # Always depend on the fetch job for this component.
        self.dependencies.append(
            JobDependency(
                JobReference(
                    component=self.component,
                    dist=None,
                    template=None,
                    stage="fetch",
                    build=None,
                )
            )
        )

        try:
            if self.has_component_packages(stage):
                self.dependencies += [
                    PluginDependency("chroot_rpm"),
                    JobDependency(
                        JobReference(
                            component=None,
                            stage="init-cache",
                            build=f"{self.dist.fullname}-{self.dist.version}-{self.dist.architecture}",
                            dist=self.dist,
                            template=None,
                        )
                    ),
                ]
        except ComponentError as e:
            raise PluginError(
                f"Cannot determine dependencies for {self.component}. Missing fetch?"
            ) from e

    @classmethod
    def matches(cls, **kwargs) -> bool:
        component = kwargs.get("component")
        if component and not component.has_packages:
            return False
        return super().matches(**kwargs)

    def run(self, **kwargs):
        super().run()
        if not self.get_parameters(self.stage).get("build", []):
            self.log.info(f"{self.component}:{self.dist}: Nothing to be done.")
            return

    def update_parameters(self, stage: str):
        super().update_parameters(stage)

        # Set and update parameters based on top-level "source",
        # per package set and per distribution.
        parameters = self.component.get_parameters(self.get_placeholders(stage))

        self._parameters[stage].update(parameters.get("source", {}))
        self._parameters[stage].update(
            parameters.get(self.dist.package_set, {}).get("source", {})
        )
        self._parameters[stage].update(
            parameters.get(self.dist.distribution, {}).get("source", {})
        )
