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

import datetime
import os
import shutil
from pathlib import Path

from qubesbuilder.common import sha256sum
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import PluginDependency
from qubesbuilder.plugins.build_windows import WinArtifactKind
from qubesbuilder.plugins.publish import PublishPlugin, PublishError


class WindowsPublishPlugin(PublishPlugin):
    dist_filter = staticmethod(lambda d: d.is_windows())
    """
    WindowsPublishPlugin manages Windows distribution publication.

    bin/          (files from the bin: entry in .qubesbuilder)
    SHA256SUMS    (if sign-key: windows is configured)
    SHA256SUMS.asc

    Stages:
        - publish
    """

    name = "publish_windows"
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
            **kwargs,
        )
        self.dependencies.append(PluginDependency("publish"))
        self.log_prefix = f"{self.component}:{self.dist}"

    def get_target_dir(self, repository_publish):
        return (
            self.config.repository_publish_dir
            / self.dist.type
            / self.config.qubes_release
            / repository_publish
            / self.dist.package_set
            / self.dist.name
        )

    def sign_digest(self, component_dir: Path, sign_key: str):
        """Generate SHA256SUMS for all published files and sign it with GPG."""
        sums = []
        for file in sorted(component_dir.rglob("*")):
            if file.is_file() and not file.name.endswith(".asc"):
                sums.append(
                    f"{sha256sum(file)}  {file.relative_to(component_dir)}"
                )

        sha256sums = component_dir / "SHA256SUMS"
        sha256sums.write_text("\n".join(sums) + "\n")

        executor = self.config.get_executor_from_config("publish", self)
        cmd = [
            f"{self.config.gpg_client} --batch --no-tty --yes --detach-sign --armor"
            f" -u {sign_key} {sha256sums} > {sha256sums}.asc"
        ]
        try:
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}: Failed to sign digest."
            raise PublishError(msg) from e

    def publish(self, build, repository_publish):
        build_bn = build.mangle()
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=build_bn
        )

        if not build_info.get("files"):
            self.log.info(f"{self.log_prefix}:{build}: Nothing to publish.")
            return

        self.log.info(
            f"{self.log_prefix}:{build}: Publishing to '{repository_publish}'."
        )

        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )
        target_dir = self.get_target_dir(repository_publish=repository_publish)
        component_dir = (
            target_dir / f"{self.component.name}_{self.component.version}"
        )
        component_dir.mkdir(parents=True, exist_ok=True)

        try:
            bin_dir = component_dir / WinArtifactKind.BIN
            bin_dir.mkdir(parents=True, exist_ok=True)
            for file in build_info["files"].get(WinArtifactKind.BIN, []):
                src = build_artifacts_dir / WinArtifactKind.BIN / file
                dst = bin_dir / file
                dst.unlink(missing_ok=True)
                os.link(src, dst)

        except (ValueError, PermissionError, NotImplementedError) as e:
            msg = f"{self.log_prefix}:{build}: Failed to publish artifacts."
            raise PublishError(msg) from e

        sign_key = self.config.sign_key.get("windows")
        if sign_key:
            if not self.config.gpg_client:
                self.log.info(
                    f"{self.log_prefix}: Please specify GPG client to use!"
                )
            else:
                self.log.info(f"{self.log_prefix}:{build}: Signing digest.")
                self.sign_digest(component_dir=component_dir, sign_key=sign_key)

    def unpublish(self, build, repository_publish):
        build_bn = build.mangle()
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=build_bn
        )

        if not build_info.get("files"):
            self.log.info(f"{self.log_prefix}:{build}: Nothing to unpublish.")
            return

        self.log.info(
            f"{self.log_prefix}:{build}: Unpublishing from '{repository_publish}'."
        )

        target_dir = self.get_target_dir(repository_publish=repository_publish)
        component_dir = (
            target_dir / f"{self.component.name}_{self.component.version}"
        )

        try:
            if component_dir.exists():
                shutil.rmtree(component_dir)
        except (ValueError, PermissionError, NotImplementedError) as e:
            msg = f"{self.log_prefix}:{build}: Failed to unpublish artifacts."
            raise PublishError(msg) from e

    def run(
        self,
        repository_publish=None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
        **kwargs,
    ):
        """
        Run plugin for given stage.
        """
        super().run()

        if not self.has_component_packages("publish"):
            return

        parameters = self.get_parameters(self.stage)

        repository_publish = (
            repository_publish
            or self.config.repository_publish.get("components")
        )
        if not repository_publish:
            raise PublishError("Cannot determine repository for publish")

        if not unpublish:
            self.validate_repository_publish(repository_publish)

            if all(
                self.is_published(
                    basename=build.mangle(), repository=repository_publish
                )
                for build in parameters["build"]
            ):
                self.log.info(
                    f"{self.log_prefix}: Already published to '{repository_publish}'."
                )
                return

            if repository_publish == "current" and not all(
                self.can_be_published_in_stable(
                    basename=build.mangle(), ignore_min_age=ignore_min_age
                )
                for build in parameters["build"]
            ):
                raise PublishError(
                    f"{self.log_prefix}: "
                    f"Refusing to publish to 'current' as packages are not "
                    f"uploaded to 'current-testing' or 'security-testing' "
                    f"for at least {self.config.min_age_days} days."
                )

            for build in parameters["build"]:
                build_bn = build.mangle()
                build_info = self.get_dist_artifacts_info(
                    stage="build", basename=build_bn
                )
                publish_info = self.get_dist_artifacts_info(
                    stage=self.stage, basename=build_bn
                )

                if not build_info:
                    raise PublishError(
                        f"{self.log_prefix}:{build}: Cannot find build info."
                    )

                # If source hash changed since last publish, unpublish old artifacts first
                info = build_info
                if publish_info:
                    if build_info.get("source-hash") != publish_info.get(
                        "source-hash"
                    ):
                        for repository in publish_info.get(
                            "repository-publish", []
                        ):
                            self.unpublish(
                                build=build,
                                repository_publish=repository["name"],
                            )
                    else:
                        info = publish_info

                self.publish(
                    build=build,
                    repository_publish=repository_publish,
                )

                info.setdefault("repository-publish", []).append(
                    {
                        "name": repository_publish,
                        "timestamp": datetime.datetime.now(
                            datetime.UTC
                        ).strftime("%Y%m%d%H%M"),
                    }
                )
                self.save_dist_artifacts_info(
                    stage="publish", basename=build_bn, info=info
                )

        if unpublish:
            if not all(
                self.is_published(
                    basename=build.mangle(), repository=repository_publish
                )
                for build in parameters["build"]
            ):
                self.log.info(
                    f"{self.log_prefix}: Not published to '{repository_publish}'."
                )
                return

            for build in parameters["build"]:
                build_bn = build.mangle()
                publish_info = self.get_dist_artifacts_info(
                    stage=self.stage, basename=build_bn
                )

                self.unpublish(
                    build=build,
                    repository_publish=repository_publish,
                )

                publish_info["repository-publish"] = [
                    r
                    for r in publish_info.get("repository-publish", [])
                    if r["name"] != repository_publish
                ]
                if publish_info.get("repository-publish", []):
                    self.save_dist_artifacts_info(
                        stage="publish", basename=build_bn, info=publish_info
                    )
                else:
                    self.log.info(
                        f"{self.log_prefix}:{build}: Removed from all repositories, deleting publish info."
                    )
                    self.delete_dist_artifacts_info(
                        stage="publish", basename=build_bn
                    )


PLUGINS = [WindowsPublishPlugin]
