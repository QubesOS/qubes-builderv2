# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2023 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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
from typing import Optional

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import ArchlinuxDistributionPlugin, PluginDependency
from qubesbuilder.plugins.publish import PublishPlugin, PublishError


class ArchlinuxRepoPlugin(ArchlinuxDistributionPlugin):
    """
    ArchlinuxPublishPlugin manages Archlinux distribution publication.

    Stages:
        - publish - Create repository to be published and uploaded to remote mirror.

    Entry points:
        - build
    """

    name = "publish_archlinux"
    stages = ["publish"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.dependencies.append(PluginDependency("publish"))

        self.log_prefix = f"{self.name}:{self.dist}"

    def sign_metadata(self, executor, directory, sign_key, repository_db):
        self.log.info(f"{self.log_prefix}:{directory}: Signing metadata.")
        repository_db_sig = repository_db.with_suffix(".gz.sig")
        cmd = [
            f"{self.config.gpg_client} --batch --no-tty --yes --detach-sign --armor -u {sign_key} {repository_db} > {repository_db_sig}",
        ]
        try:
            executor.run(cmd)
        except (ExecutorError, OSError) as e:
            # On error, it creates an empty file.
            repository_db_sig.unlink(missing_ok=True)
            msg = f"{self.log_prefix}:{directory}:  Failed to sign metadata"
            raise PublishError(msg) from e


class ArchlinuxPublishPlugin(ArchlinuxRepoPlugin, PublishPlugin):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.log_prefix = f"{self.component}:{self.dist}"

    def publish(
        self, executor, directory, keyring_dir, sign_key, repository_publish
    ):
        # directory basename will be used as prefix for some artifacts
        directory_bn = directory.mangle()

        # Build artifacts (source included)
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )

        # Publish repository
        artifacts_dir = self.config.repository_publish_dir / self.dist.type

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=directory_bn
        )

        if not build_info.get("packages", None):
            self.log.info(f"{self.log_prefix}:{directory}: Nothing to publish.")
            return

        self.log.info(f"{self.log_prefix}:{directory}: Publishing packages.")

        packages_list = [
            build_artifacts_dir / "pkgs" / pkg for pkg in build_info["packages"]
        ]

        # Verify signatures (sanity check, refuse to publish if packages weren't signed)
        try:
            self.log.info(
                f"{self.log_prefix}:{directory}: Verifying signatures."
            )
            cmd = []
            for pkg in packages_list:
                cmd += [
                    f"gpg2 -q --homedir {keyring_dir} --verify {pkg}.sig {pkg}"
                ]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}:{directory}: Failed to check signatures."
            raise PublishError(msg) from e

        # Publishing packages
        cmd = []
        try:
            target_dir = (
                artifacts_dir
                / f"{self.config.qubes_release}/{repository_publish}/{self.dist.package_set}/{self.dist.name}"
            )
            repository_db = (
                target_dir
                / f"pkgs/qubes-{self.config.qubes_release}-{repository_publish}.db.tar.gz"
            )
            repository_db.parent.mkdir(parents=True, exist_ok=True)
            if not repository_db.exists():
                cmd += [f"repo-add {repository_db}"]
            for pkg in packages_list:
                target_path = target_dir / "pkgs" / pkg.name
                target_sig_path = target_path.with_suffix(".zst.sig")

                target_path.unlink(missing_ok=True)
                target_sig_path.unlink(missing_ok=True)

                os.link(pkg, target_path)
                os.link(pkg.with_suffix(".zst.sig"), target_sig_path)

                cmd += [f"repo-add {repository_db} {pkg}"]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}:{directory}: Failed to publish packages."
            raise PublishError(msg) from e

        self.sign_metadata(executor, directory, sign_key, repository_db)

    def unpublish(self, executor, directory, sign_key, repository_publish):
        # directory basename will be used as prefix for some artifacts
        directory_bn = directory.mangle()

        # Build artifacts (source included)
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )

        # Publish repository
        artifacts_dir = self.config.repository_publish_dir / self.dist.type

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=directory_bn
        )

        if not build_info.get("packages", None):
            self.log.info(
                f"{self.log_prefix}:{directory}: Nothing to unpublish."
            )
            return

        self.log.info(f"{self.log_prefix}:{directory}: Unpublishing packages.")

        packages_list = [
            build_artifacts_dir / "pkgs" / pkg for pkg in build_info["packages"]
        ]

        # Unpublishing packages
        cmd = []
        try:
            target_dir = (
                artifacts_dir
                / f"{self.config.qubes_release}/{repository_publish}/{self.dist.package_set}/{self.dist.name}"
            )
            repository_db = (
                target_dir
                / f"pkgs/qubes-r{self.config.qubes_release}-{repository_publish}.db.tar.gz"
            )
            if not repository_db.exists:
                return
            for pkg in packages_list:
                target_path = target_dir / "pkgs" / pkg.name
                target_sig_path = target_path.with_suffix(".zst.sig")

                target_path.unlink(missing_ok=True)
                target_sig_path.unlink(missing_ok=True)

                # repo-remove supports only package name as input, not version and release.
                pkg_name = pkg.name.replace(
                    f"-{self.component.get_version_release()}-{self.dist.architecture}.pkg.tar.zst",
                    "",
                )
                cmd += [f"repo-remove {repository_db} {pkg_name}"]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.log_prefix}:{directory}: Failed to publish packages."
            raise PublishError(msg) from e

        self.sign_metadata(executor, directory, sign_key, repository_db)

    def run(
        self,
        repository_publish: Optional[str] = None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
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
        ) or self.config.sign_key.get("archlinux", None)
        if not sign_key:
            self.log.info(f"{self.log_prefix}: No signing key found.")
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            self.log.info(
                f"{self.component}: Please specify GPG client to use!"
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
                    f"{self.log_prefix}: Already published to '{repository_publish}'."
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
                    f"{self.log_prefix}: "
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
                        f"{self.log_prefix}:{directory}: Cannot find build info."
                    )

                # If previous publication to a repo has been done and does not correspond to current
                # build artifacts, we delete previous publications. It happens if we modify local
                # sources and then publish into repository with the same version and release.
                info = build_info
                if publish_info:
                    if build_info["source-hash"] != publish_info["source-hash"]:
                        self.log.info(
                            f"{self.log_prefix}:{directory}: Current build hash does not match previous one."
                        )
                        for repository in publish_info.get(
                            "repository-publish", []
                        ):
                            self.unpublish(
                                executor=self.executor,
                                directory=directory,
                                sign_key=sign_key,
                                repository_publish=repository,
                            )
                    else:
                        info = publish_info

                self.publish(
                    executor=self.executor,
                    directory=directory,
                    keyring_dir=keyring_dir,
                    sign_key=sign_key,
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
                    sign_key=sign_key,
                    repository_publish=repository_publish,
                )

                # Save package information we published for committing into current. If the packages
                # are not published into another repository, we delete the "publish" stage information.
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
                        f"{self.log_prefix}:{directory}: Not published anywhere else, deleting publish info."
                    )
                    self.delete_dist_artifacts_info(
                        stage="publish", basename=directory_bn
                    )


PLUGINS = [ArchlinuxPublishPlugin]
