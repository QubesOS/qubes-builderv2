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
    DistributionComponentPlugin,
    PluginError,
    JobDependency,
    JobReference,
)


class BuildError(PluginError):
    pass


class BuildPlugin(DistributionComponentPlugin):
    """
    BuildPlugin manages generic distribution build.

    Stages:
        - build - Ensure all build targets artifacts exist from previous required stage.

    Entry points:
        - build
    """

    name = "build"

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

        try:
            if self.has_component_packages(stage="build"):
                for build in self.get_parameters(stage="build").get(
                    "build", []
                ):
                    self.dependencies.append(
                        JobDependency(
                            JobReference(
                                component=self.component,
                                dist=self.dist,
                                stage="prep",
                                build=build.mangle(),
                                template=None,
                            )
                        )
                    )
        except ComponentError as e:
            raise PluginError(
                f"Cannot determine dependencies for {self.component}. Missing fetch?"
            ) from e

    @classmethod
    def from_args(cls, **kwargs):
        component = kwargs.get("component")
        if component and not component.has_packages:
            return None
        return super().from_args(**kwargs)
