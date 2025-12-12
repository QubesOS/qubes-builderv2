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
from typing import Optional

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import DEBDistributionPlugin, PluginDependency
from qubesbuilder.plugins.publish import PublishPlugin, PublishError


class DEBRepoPlugin(DEBDistributionPlugin):
    """
    DEBPublishPlugin manages Debian distribution publication.

    Stages:
        - publish - Create repository to be published and uploaded to remote mirror.

    Entry points:
        - build
    """

    name = "publish_deb"
    stages = ["publish"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.log_prefix = f"{self.name}:{self.dist}"

        self.dependencies.append(PluginDependency("publish"))

    @classmethod
    def get_debian_suite_from_repository_publish(cls, dist, repository_publish):
        # set debian suite according to publish repository
        debian_suite = dist.name
        if repository_publish == "current-testing":
            debian_suite += "-testing"
        elif repository_publish == "security-testing":
            debian_suite += "-securitytesting"
        elif repository_publish == "unstable":
            debian_suite += "-unstable"
        return debian_suite

    def get_target_dir(self):
        artifacts_dir = self.config.repository_publish_dir / self.dist.type
        return (
            artifacts_dir
            / f"{self.config.qubes_release}/{self.dist.package_set}"
        )

    def create_repository_skeleton(self):
        artifacts_dir = self.config.repository_publish_dir / self.dist.type

        # Create publish repository skeleton
        create_skeleton_cmd = [
            f"{self.manager.entities['publish_deb'].directory}/scripts/create-skeleton",
            self.config.qubes_release,
            self.dist.fullname,
            str(artifacts_dir),
        ]
        cmd = [" ".join(create_skeleton_cmd)]
        try:
            executor = self.config.get_executor_from_config("publish", self)
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}: Failed to create repository skeleton."
            raise PublishError(msg) from e

    def create_metadata(self, repository_publish):
        try:
            reprepro_options = f"-b {self.get_target_dir()}"

            debian_suite = self.get_debian_suite_from_repository_publish(
                self.dist, repository_publish
            )

            # reprepro command
            cmd = [f"reprepro {reprepro_options} export {debian_suite}"]
            executor = self.config.get_executor_from_config("publish", self)
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}: Failed to create metadata."
            raise PublishError(msg) from e

    def sign_metadata(self, repository_publish):
        """Sign repository metadata

        Do it manually, as reprepro does not support alternative gpg client"""

        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("deb", None)

        if not sign_key:
            self.log.info(f"{self.log_prefix}: No signing key found.")
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            self.log.info(
                f"{self.log_prefix}: Please specify GPG client to use!"
            )
            return

        debian_suite = self.get_debian_suite_from_repository_publish(
            dist=self.dist, repository_publish=repository_publish
        )

        for opt, out_name in (
            ("--detach-sign", "Release.gpg"),
            ("--clearsign", "InRelease"),
        ):
            try:
                release_dir = self.get_target_dir() / "dists" / debian_suite
                cmd = [
                    f"{self.config.gpg_client} {opt} --armor --local-user {sign_key} "
                    f"--batch --no-tty --output {release_dir / out_name} {release_dir / 'Release'}"
                ]
                self.log.info(
                    f"{self.log_prefix}: Signing metadata ({out_name})."
                )
                executor = self.config.get_executor_from_config("publish", self)
                executor.run(cmd)
            except ExecutorError as e:
                msg = (
                    f"{self.log_prefix}: Failed to sign metadata ({out_name})."
                )
                raise PublishError(msg) from e

    def create(self, repository_publish: Optional[str]):
        if not repository_publish:
            self.log.error(
                "Cannot create repository without a repository name!"
            )

        # Create skeleton
        self.create_repository_skeleton()

        # Create metadata
        self.create_metadata(repository_publish=repository_publish)

        # Sign metadata
        self.sign_metadata(repository_publish=repository_publish)

    def run(
        self,
        repository_publish: Optional[str] = None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
        create_and_sign_metadata_only: bool = False,
        **kwargs,
    ):
        if create_and_sign_metadata_only:
            self.create(repository_publish)
        else:
            super().run()


class DEBPublishPlugin(DEBRepoPlugin, PublishPlugin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.log_prefix = f"{self.component}:{self.dist}"

    def publish(self, executor, directory, keyring_dir, repository_publish):
        # directory basename will be used as prefix for some artifacts
        directory_bn = directory.mangle()

        # Build artifacts (source included)
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=directory_bn
        )

        if not build_info.get("changes", None):
            self.log.info(f"{self.log_prefix}:{directory}: Nothing to publish.")
            return

        self.log.info(
            f"{self.component}:{self.dist}:{directory}: Publishing packages."
        )

        # Verify signatures (sanity check, refuse to publish if packages weren't signed)
        try:
            self.log.info(
                f"{self.log_prefix}:{directory}: Verifying signatures."
            )
            cmd = []
            for file in ("dsc", "changes", "buildinfo"):
                fname = build_artifacts_dir / build_info[file]
                cmd += [f"gpg2 -q --homedir {keyring_dir} --verify {fname}"]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}:{directory}: Failed to check signatures."
            raise PublishError(msg) from e

        # Publishing packages
        try:
            changes_file = build_artifacts_dir / build_info["changes"]
            target_dir = self.get_target_dir()

            # reprepro options to ignore surprising binary and arch
            reprepro_options = f"--ignore=surprisingbinary --ignore=surprisingarch --keepunreferencedfiles -b {target_dir}"

            debian_suite = self.get_debian_suite_from_repository_publish(
                self.dist, repository_publish
            )

            # reprepro command
            cmd = [
                f"reprepro {reprepro_options} include {debian_suite} {changes_file}"
            ]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}:{directory}: Failed to publish packages."
            raise PublishError(msg) from e

        self.sign_metadata(repository_publish=repository_publish)

    def unpublish(self, executor, directory, repository_publish):
        # directory basename will be used as prefix for some artifacts
        directory_bn = directory.mangle()

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=directory_bn
        )

        if not build_info.get("changes", None):
            self.log.info(f"{self.log_prefix}:{directory}: Nothing to publish.")
            return

        self.log.info(f"{self.log_prefix}:{directory}: Unpublishing packages.")

        # Unpublishing packages
        try:
            target_dir = self.get_target_dir()

            # reprepro options to ignore surprising binary and arch
            reprepro_options = f"--ignore=surprisingbinary --ignore=surprisingarch -b {target_dir}"

            # set debian suite according to publish repository
            debian_suite = self.get_debian_suite_from_repository_publish(
                self.dist, repository_publish
            )

            # reprepro command
            source_name, source_version = build_info[
                "package-release-name-full"
            ].split("_", 1)
            cmd = [
                f"reprepro {reprepro_options} removefilter {debian_suite} '$Source (=={source_name}), $Version (=={source_version})'"
            ]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}:{directory}: Failed to unpublish packages."
            raise PublishError(msg) from e

        self.sign_metadata(repository_publish=repository_publish)

    def run(
        self,
        repository_publish: Optional[str] = None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
        create_and_sign_metadata_only: bool = False,
        **kwargs,
    ):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("publish"):
            return

        parameters = self.get_parameters(self.stage)

        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("deb", None)

        if not sign_key:
            self.log.info(f"{self.log_prefix}: No signing key found.")
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            self.log.info(
                f"{self.log_prefix}: Please specify GPG client to use!"
            )
            return

        # Sign artifacts
        sign_artifacts_dir = self.get_dist_component_artifacts_dir(stage="sign")

        # Keyring used for signing
        keyring_dir = sign_artifacts_dir / "keyring"

        repository_publish = (
            repository_publish
            or self.config.repository_publish.get("components")
        )
        if not repository_publish:
            raise PublishError("Cannot determine repository for publish")

        if not unpublish:
            if not sign_artifacts_dir.exists():
                raise PublishError("Cannot find keyring from sign stage.")

            # Create skeleton
            self.create_repository_skeleton()

            # Check if publish repository is valid
            self.validate_repository_publish(repository_publish)

            # Check if we already published packages into the provided repository
            if all(
                self.is_published(
                    basename=directory.mangle(),
                    repository=repository_publish,
                )
                for directory in parameters["build"]
            ):
                self.log.info(
                    f"{self.component}:{self.dist}: Already published to '{repository_publish}'."
                )
                return

            # Check if we can publish into current
            if repository_publish == "current" and not all(
                self.can_be_published_in_stable(
                    basename=directory.mangle(),
                    ignore_min_age=ignore_min_age,
                )
                for directory in parameters["build"]
            ):
                failure_msg = (
                    f"{self.component}:{self.dist}: "
                    f"Refusing to publish to 'current' as packages are not "
                    f"uploaded to 'current-testing' or 'security-testing' "
                    f"for at least {self.config.min_age_days} days."
                )
                raise PublishError(failure_msg)

            for directory in parameters["build"]:
                # directory basename will be used as prefix for some artifacts
                directory_bn = directory.mangle()

                build_info = self.get_dist_artifacts_info(
                    stage="build", basename=directory_bn
                )
                publish_info = self.get_dist_artifacts_info(
                    stage=self.stage, basename=directory_bn
                )

                if not build_info:
                    raise PublishError(
                        f"{self.component}:{self.dist}:{directory}: Cannot find build info."
                    )

                # If previous publication to a repo has been done and does not correspond to current
                # build artifacts, we delete previous publications. It happens if we modify local
                # sources and then publish into repository with the same version and release.
                info = build_info
                if publish_info:
                    if build_info["source-hash"] != publish_info["source-hash"]:
                        self.log.info(
                            f"{self.component}:{self.dist}:{directory}: Current build hash does not match previous one."
                        )
                        for repository in publish_info.get(
                            "repository-publish", []
                        ):
                            self.unpublish(
                                executor=self.executor,
                                directory=directory,
                                repository_publish=repository,
                            )
                    else:
                        info = publish_info

                self.publish(
                    executor=self.executor,
                    directory=directory,
                    keyring_dir=keyring_dir,
                    repository_publish=repository_publish,
                )

                # Save package information we published for committing into current
                info.setdefault("repository-publish", []).append(
                    {
                        "name": repository_publish,
                        "timestamp": datetime.datetime.now(
                            datetime.UTC
                        ).strftime("%Y%m%d%H%M"),
                    }
                )
                self.save_dist_artifacts_info(
                    stage="publish", basename=directory_bn, info=info
                )

        if unpublish:
            if not all(
                self.is_published(
                    basename=directory.mangle(),
                    repository=repository_publish,
                )
                for directory in parameters["build"]
            ):
                self.log.info(
                    f"{self.log_prefix}: Not published to '{repository_publish}'."
                )
                return

            for directory in parameters["build"]:
                # directory basename will be used as prefix for some artifacts
                directory_bn = directory.mangle()

                publish_info = self.get_dist_artifacts_info(
                    stage=self.stage, basename=directory_bn
                )

                self.unpublish(
                    executor=self.executor,
                    directory=directory,
                    repository_publish=repository_publish,
                )

                # Save package information we published for committing into current. If the packages
                # are not published into another repository, we delete the publish stage information.
                publish_info["repository-publish"] = [
                    r
                    for r in publish_info.get("repository-publish", [])
                    if r["name"] != repository_publish
                ]
                if publish_info.get("repository-publish", []):
                    self.save_dist_artifacts_info(
                        stage="publish",
                        basename=directory_bn,
                        info=publish_info,
                    )
                else:
                    self.log.info(
                        f"{self.component}:{self.dist}:{directory}: Not published anywhere else, deleting publish info."
                    )
                    self.delete_dist_artifacts_info(
                        stage="publish", basename=directory_bn
                    )

                # We republish previous package version that has been published previously in the
                # same repository. This is because reprepro does not manage multiversions officially.
                for (
                    artifacts_dir
                ) in self.get_dist_component_artifacts_dir_history(
                    stage=self.stage
                ):
                    publish_info = self.get_dist_artifacts_info(
                        stage=self.stage,
                        basename=directory_bn,
                        artifacts_dir=artifacts_dir,
                    )
                    if repository_publish in publish_info.get(
                        "repository-publish", []
                    ):
                        self.log.info(
                            f"{self.component}:{self.dist}:{directory}: Updating repository."
                        )
                        self.publish(
                            executor=self.executor,
                            directory=directory,
                            keyring_dir=keyring_dir,
                            repository_publish=repository_publish,
                        )
                        break


PLUGINS = [DEBRepoPlugin, DEBPublishPlugin]
