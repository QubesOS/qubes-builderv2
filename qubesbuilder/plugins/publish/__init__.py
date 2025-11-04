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

import datetime

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.log import QubesBuilderLogger
from qubesbuilder.plugins import (
    DistributionComponentPlugin,
    PluginError,
    JobDependency,
    JobReference,
)

# Define the minimum age for which packages can be published to 'current'
COMPONENT_REPOSITORIES = [
    "current",
    "current-testing",
    "security-testing",
    "unstable",
]


class PublishError(PluginError):
    pass


class PublishPlugin(DistributionComponentPlugin):
    """
    PublishPlugin manages generic distribution publication.

    Stages:
        - publish - Ensure all build targets artifacts exist from previous required stages.

    Entry points:
        - build
    """

    _publish_not_configured_warned = False
    name = "publish"
    stages = ["publish"]

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
    def is_publish_configured(cls, config, dist, component):
        if not cls.supported_distribution(dist):
            return False
        if not cls.is_signing_configured(config, dist, component):
            return False
        if not config.repository_publish.get("components"):
            if not cls._publish_not_configured_warned:
                QubesBuilderLogger.info(
                    f"{cls.name}:{dist}: 'repository-publish:components' not set."
                )
                cls._publish_not_configured_warned = True
            return False
        return True

    @classmethod
    def from_args(cls, **kwargs):
        component = kwargs.get("component")
        config = kwargs.get("config")
        dist = kwargs.get("dist")
        stage = kwargs.get("stage")
        if stage != "publish":
            return None
        if component and not component.has_packages:
            return None
        if not cls.is_publish_configured(config, dist, component):
            return None
        return super().from_args(**kwargs)

    def validate_repository_publish(self, repository_publish):
        if repository_publish not in (
            "current",
            "current-testing",
            "security-testing",
            "unstable",
        ):
            msg = (
                f"{self.component}:{self.dist}: "
                f"Refusing to publish components into '{repository_publish}'."
            )
            raise PublishError(msg)

    def is_published(self, basename, repository):
        publish_info = self.get_dist_artifacts_info(
            stage="publish", basename=basename
        )
        if not publish_info:
            return False
        return repository in [
            r["name"] for r in publish_info.get("repository-publish", [])
        ]

    def can_be_published_in_stable(self, basename, ignore_min_age):
        # Check packages are published
        if not self.is_published(basename, "current-testing"):
            return False

        # Check minimum day that packages are available for testing
        publish_info = self.get_dist_artifacts_info(
            stage="publish", basename=basename
        )
        publish_date = None
        for r in publish_info["repository-publish"]:
            if r["name"] == "current-testing":
                publish_date = datetime.datetime.strptime(
                    r["timestamp"] + "Z", "%Y%m%d%H%M%z"
                )
                break

        if not publish_date:
            self.log.error(
                "Something wrong detected in repositories. Missing timestamp?"
            )
            return False

        # Check that packages have been published before threshold_date
        threshold_date = datetime.datetime.now(
            datetime.UTC
        ) - datetime.timedelta(days=self.config.min_age_days)
        if not ignore_min_age and publish_date > threshold_date:
            return False

        return True

    def create(self, repository_publish: str):
        return

    def run(self, **kwargs):
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("publish"):
            return

        if not isinstance(self.executor, LocalExecutor):
            raise PublishError("This plugin only supports local executor.")
