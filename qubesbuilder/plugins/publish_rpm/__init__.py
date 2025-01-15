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
import os
import shutil

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.plugins import RPMDistributionPlugin, PluginDependency
from qubesbuilder.plugins.publish import PublishPlugin, PublishError


class RPMPublishPlugin(RPMDistributionPlugin, PublishPlugin):
    """
    RPMPublishPlugin manages RPM distribution publication.

    Stages:
        - publish - Create repository to be published and uploaded to remote mirror.

    Entry points:
        - build
    """

    name = "publish_rpm"
    stages = ["publish"]
    dependencies = [PluginDependency("publish"), PluginDependency("sign_rpm")]

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config: Config,
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(
            component=component, dist=dist, config=config, manager=manager
        )

    def get_target_dir(self, repository_publish):
        artifacts_dir = self.config.repository_publish_dir / self.dist.type
        return (
            artifacts_dir
            / f"{self.config.qubes_release}/{repository_publish}/{self.dist.package_set}/{self.dist.name}"
        )

    def create_repository_skeleton(self):
        # marmarek: should this be done only if not exists yet?
        # fepitre: creating skeleton is harmless and allows to update comps.xml if needed

        # Create publish repository skeleton
        comps = (
            self.config.sources_dir
            / f"qubes-release/comps/comps-{self.dist.package_set}.xml"
        )
        if not comps.exists():
            self.log.warning(
                f"Cannot find {comps}. Have you added qubes-release to components?"
            )
        artifacts_dir = self.config.repository_publish_dir / self.dist.type

        create_skeleton_cmd = [
            f"{self.manager.entities['publish_rpm'].directory}/scripts/create-skeleton",
            self.config.qubes_release,
            self.dist.package_set,
            self.dist.name,
            str(artifacts_dir.absolute()),
            str(comps),
        ]
        cmd = [" ".join(create_skeleton_cmd)]

        try:
            executor = self.config.get_executor_from_config("publish", self)
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}: Failed to create repository skeleton."
            raise PublishError(msg) from e

    def createrepo(self, executor, target_dir):
        self.log.info(f"{self.component}:{self.dist}: Updating metadata.")
        cmd = [f"cd {target_dir}"]
        if (
            self.config.sources_dir
            / f"qubes-release/comps/comps-{self.dist.package_set}.xml"
        ).exists():
            cmd.append("createrepo_c --update -g comps.xml .")
        else:
            cmd.append("createrepo_c --update .")
        try:
            shutil.rmtree(target_dir / "repodata")
            executor.run(cmd)
        except (ExecutorError, OSError) as e:
            msg = f"{self.component}:{self.dist}: Failed to 'createrepo_c'"
            raise PublishError(msg) from e

    def sign_metadata(self, executor, sign_key, target_dir):
        self.log.info(f"{self.component}:{self.dist}: Signing metadata.")
        repomd = target_dir / "repodata/repomd.xml"
        cmd = [
            f"{self.config.gpg_client} --batch --no-tty --yes --detach-sign --armor -u {sign_key} {repomd} > {repomd}.asc",
        ]
        try:
            executor.run(cmd)
        except (ExecutorError, OSError) as e:
            msg = f"{self.component}:{self.dist}: Failed to sign metadata."
            raise PublishError(msg) from e

    def create_metalink(self, executor, repository_publish):
        repo_basedir = self.config.repository_publish_dir / self.dist.type
        repository_dir = (
            repo_basedir
            / self.config.qubes_release
            / repository_publish
            / self.dist.package_set
            / self.dist.name
        )
        repomd = repository_dir / "repodata/repomd.xml"
        if not repomd.exists():
            msg = (
                f"{self.component}:{self.dist}: Cannot find repomd '{repomd}'."
            )
            raise PublishError(msg)

        self.log.info(f"Creating metalink for {repomd}.")
        try:
            cmd = [
                f"python3 {self.manager.entities['publish'].directory}/mirrors/qubesmirror/metalink.py -b {repo_basedir} -- {self.manager.entities['publish_rpm'].directory}/mirrors.list {repomd} > {repomd}.metalink"
            ]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}: Failed to create metalink for '{repomd}': {str(e)}"
            self.log.error(msg)

    def create_and_sign_repository_metadata(self, repository_publish):
        executor = self.config.get_executor_from_config("publish", self)
        target_dir = self.get_target_dir(repository_publish=repository_publish)
        (target_dir / "repodata").mkdir(parents=True, exist_ok=True)

        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("rpm", None)

        if not sign_key:
            self.log.info(
                f"{self.component}:{self.dist}: No signing key found."
            )
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            self.log.info(
                f"{self.component}: Please specify GPG client to use!"
            )
            return

        # Createrepo unpublished RPMs
        self.createrepo(executor=executor, target_dir=target_dir)

        # Sign metadata
        self.sign_metadata(
            executor=executor, sign_key=sign_key, target_dir=target_dir
        )

        # Create metalink
        self.create_metalink(
            executor=executor, repository_publish=repository_publish
        )

    def publish(self, executor, build, sign_key, db_path, repository_publish):
        # spec file basename will be used as prefix for some artifacts
        build_bn = build.mangle()

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=build_bn
        )

        if not build_info.get("rpms", []) and not build_info.get("srpm", None):
            self.log.info(
                f"{self.component}:{self.dist}:{build}: Nothing to publish."
            )
            return

        # Publish packages with hardlinks to built RPMs
        self.log.info(
            f"{self.component}:{self.dist}:{build}: Publishing RPMs to '{repository_publish}'."
        )

        # Source artifacts
        prep_artifacts_dir = self.get_dist_component_artifacts_dir(stage="prep")
        # Build artifacts
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )

        packages_list = [
            build_artifacts_dir / "rpm" / rpm for rpm in build_info["rpms"]
        ]
        packages_list += [prep_artifacts_dir / build_info["srpm"]]

        # We check that signature exists (--check-only option)
        self.log.info(
            f"{self.component}:{self.dist}:{build}: Verifying signatures."
        )
        try:
            for rpm in packages_list:
                cmd = [
                    f"{self.manager.entities['sign_rpm'].directory}/scripts/sign-rpm "
                    f"--sign-key {sign_key} --db-path {db_path} --rpm {rpm} --check-only"
                ]
                executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}:{build}: Failed to check signatures."
            raise PublishError(msg) from e

        target_dir = self.get_target_dir(repository_publish=repository_publish)
        try:
            # srpc and rpms
            for rpm in packages_list:
                target_path = target_dir / "rpm" / rpm.name
                target_path.unlink(missing_ok=True)
                # target_path.hardlink_to(rpm)
                os.link(rpm, target_path)

            # buildinfo
            target_path = target_dir / "rpm" / build_info["buildinfo"]
            target_path.unlink(missing_ok=True)
            os.link(
                build_artifacts_dir / "rpm" / build_info["buildinfo"],
                target_path,
            )
        except (ValueError, PermissionError, NotImplementedError) as e:
            msg = f"{self.component}:{self.dist}:{build}: Failed to publish packages."
            raise PublishError(msg) from e

        # Create and sign metadata
        self.create_and_sign_repository_metadata(
            repository_publish=repository_publish
        )

    def unpublish(self, build, repository_publish):
        # spec file basename will be used as prefix for some artifacts
        build_bn = build.mangle()
        # Read information from build stage
        build_info = self.get_dist_artifacts_info(
            stage="build", basename=build_bn
        )

        if not build_info.get("rpms", []) and not build_info.get("srpm", None):
            self.log.info(
                f"{self.component}:{self.dist}:{build}: Nothing to unpublish."
            )
            return

        self.log.info(
            f"{self.component}:{self.dist}:{build}: Unpublishing RPMs from '{repository_publish}'."
        )

        # Source artifacts
        prep_artifacts_dir = self.get_dist_component_artifacts_dir(stage="prep")
        # Build artifacts
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )

        packages_list = [
            build_artifacts_dir / "rpm" / rpm for rpm in build_info["rpms"]
        ]
        packages_list += [prep_artifacts_dir / build_info["srpm"]]

        # If exists, remove hardlinks to built RPMs
        target_dir = self.get_target_dir(repository_publish=repository_publish)
        try:
            for rpm in packages_list:
                target_path = target_dir / "rpm" / rpm.name
                target_path.unlink(missing_ok=True)
        except (ValueError, PermissionError, NotImplementedError) as e:
            msg = f"{self.component}:{self.dist}:{build}: Failed to unpublish packages."
            raise PublishError(msg) from e

        # Create and sign metadata
        self.create_and_sign_repository_metadata(
            repository_publish=repository_publish
        )

    def create(self, repository_publish: str):
        # Create skeleton
        self.create_repository_skeleton()

        # Create and sign metadata
        self.create_and_sign_repository_metadata(
            repository_publish=repository_publish
        )

    def run(
        self,
        stage: str,
        repository_publish: str = None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
        **kwargs,
    ):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run(stage=stage)

        if stage != "publish" or not self.has_component_packages("publish"):
            return

        executor = self.get_executor_from_config(stage)
        parameters = self.get_parameters(stage)

        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("rpm", None)

        if not sign_key:
            self.log.info(
                f"{self.component}:{self.dist}: No signing key found."
            )
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            self.log.info(
                f"{self.component}: Please specify GPG client to use!"
            )
            return

        # FIXME: Refactor the code handling both standard and template components.
        #  It applies for other plugins.

        repository_publish = (
            repository_publish
            or self.config.repository_publish.get("components")
        )
        if not repository_publish:
            raise PublishError("Cannot determine repository for publish")

        # Publish stage for standard (not template) components
        if not unpublish:
            # repository-publish directory
            artifacts_dir = self.config.repository_publish_dir / self.dist.type

            # Ensure dbpath from sign stage (still) exists
            db_path = self.config.artifacts_dir / f"rpmdb/{sign_key}"
            if not db_path.exists():
                msg = f"{self.component}: {self.dist}: Failed to find RPM DB path."
                raise PublishError(msg)

            # Create skeleton
            self.create_repository_skeleton()

            # Check if publish repository is valid
            self.validate_repository_publish(repository_publish)

            # Check if we already published packages into the provided repository
            if all(
                self.is_published(
                    basename=build.mangle(), repository=repository_publish
                )
                for build in parameters["build"]
            ):
                self.log.info(
                    f"{self.component}:{self.dist}: Already published to '{repository_publish}'."
                )
                # Update metalink in case of previous failure on creating it
                self.create_metalink(
                    executor=executor, repository_publish=repository_publish
                )
                return

            # Check if we can publish into current
            if repository_publish == "current" and not all(
                self.can_be_published_in_stable(
                    basename=build.mangle(), ignore_min_age=ignore_min_age
                )
                for build in parameters["build"]
            ):
                failure_msg = (
                    f"{self.component}:{self.dist}: "
                    f"Refusing to publish to 'current' as packages are not "
                    f"uploaded to 'current-testing' or 'security-testing' "
                    f"for at least {self.config.min_age_days} days."
                )
                raise PublishError(failure_msg)

            for build in parameters["build"]:
                build_bn = build.mangle()
                build_info = self.get_dist_artifacts_info(
                    stage="build", basename=build_bn
                )
                publish_info = self.get_dist_artifacts_info(
                    stage=stage, basename=build_bn
                )

                if not build_info:
                    raise PublishError(
                        f"{self.component}:{self.dist}:{build}: Cannot find build info."
                    )

                # If previous publication to a repo has been done and does not correspond to current
                # build artifacts, we delete previous publications. It happens if we modify local
                # sources and then publish into repository with the same version and release.
                info = build_info
                if publish_info:
                    if build_info["source-hash"] != publish_info["source-hash"]:
                        for repository in publish_info.get(
                            "repository-publish", []
                        ):
                            self.unpublish(
                                build=build,
                                repository_publish=repository,
                            )
                    else:
                        info = publish_info

                self.publish(
                    executor=executor,
                    build=build,
                    sign_key=sign_key,
                    db_path=db_path,
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
                    f"{self.component}:{self.dist}: Not published to '{repository_publish}'."
                )
                return

            for build in parameters["build"]:
                build_bn = build.mangle()
                publish_info = self.get_dist_artifacts_info(
                    stage=stage, basename=build_bn
                )

                self.unpublish(
                    build=build,
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
                        stage="publish", basename=build_bn, info=publish_info
                    )
                else:
                    self.log.info(
                        f"{self.component}:{self.dist}:{build_bn}: Not published anywhere else, deleting publish info."
                    )
                    self.delete_dist_artifacts_info(
                        stage="publish", basename=build_bn
                    )


PLUGINS = [RPMPublishPlugin]
