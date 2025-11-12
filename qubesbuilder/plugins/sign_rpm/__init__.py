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
import shutil
import tempfile
from pathlib import Path

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import RPMDistributionPlugin, PluginDependency
from qubesbuilder.plugins.build import BuildError
from qubesbuilder.plugins.build_rpm import (
    provision_local_repository,
    clean_local_repository,
)
from qubesbuilder.plugins.sign import SignPlugin, SignError


class RPMSignPlugin(RPMDistributionPlugin, SignPlugin):
    """
    RPMSignPlugin manages RPM distribution sign.

    Stages:
        - sign - Sign SRPM, RPMs and buildinfo file.

    Entry points:
        - build
    """

    name = "sign_rpm"
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
        self.dependencies.append(PluginDependency("sign"))

    def run(self, **kwargs):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("sign"):
            return

        parameters = self.get_parameters(self.stage)

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

        # Sign stage for standard components

        # Source artifacts
        prep_artifacts_dir = self.get_dist_component_artifacts_dir(stage="prep")
        # Build artifacts
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )

        # RPMDB
        db_path = self.config.artifacts_dir / f"rpmdb/{sign_key}"

        temp_dir = Path(tempfile.mkdtemp())
        sign_key_asc = temp_dir / f"{sign_key}.asc"
        cmd = [
            f"mkdir -p {db_path}",
            f"{self.config.gpg_client} --armor --export {sign_key} > {sign_key_asc}",
            f"rpmkeys --dbpath={db_path} --import {sign_key_asc}",
        ]
        try:
            self.executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}: Failed to create RPM dbpath."
            raise SignError(msg) from e
        finally:
            # Clear temporary dir
            shutil.rmtree(temp_dir)

        # Prepare for re-provisioning local directory
        repository_dir = self.config.repository_dir / self.dist.distribution
        clean_local_repository(
            self.log, repository_dir, self.component, self.dist, False
        )

        for build in parameters["build"]:
            # spec file basename will be used as prefix for some artifacts
            build_bn = build.mangle()

            # Read information from build stage
            build_info = self.get_dist_artifacts_info(
                stage="build", basename=build_bn
            )

            if not build_info.get("rpms", []) and not build_info.get(
                "srpm", None
            ):
                self.log.info(
                    f"{self.component}:{self.dist}:{build}: Nothing to sign."
                )
                continue

            packages_list = [
                build_artifacts_dir / "rpm" / rpm for rpm in build_info["rpms"]
            ]
            packages_list += [prep_artifacts_dir / build_info["srpm"]]

            try:
                for rpm in packages_list:
                    self.log.info(
                        f"{self.component}:{self.dist}:{build}: Signing '{rpm.name}'."
                    )
                    cmd = [
                        f"{self.manager.entities['sign_rpm'].directory}/scripts/sign-rpm "
                        f"--sign-key {sign_key} --db-path {db_path} --rpm {rpm}"
                    ]
                    self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to sign RPMs."
                raise SignError(msg) from e

            buildinfo_file = (
                build_artifacts_dir / "rpm" / build_info["buildinfo"]
            )

            try:
                self.log.info(
                    f"{self.component}:{self.dist}:{build}: Signing '{buildinfo_file.name}'."
                )
                cmd = [
                    f"{self.manager.entities['sign_rpm'].directory}/scripts/update-rpmbuildinfo {buildinfo_file} {self.config.gpg_client} {sign_key}"
                ]
                self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to sign buildinfo file."
                raise SignError(msg) from e

            # Re-provision builder local repository with signatures
            try:
                provision_local_repository(
                    log=self.log,
                    build=build,
                    component=self.component,
                    dist=self.dist,
                    repository_dir=repository_dir,
                    source_info=build_info,
                    packages_list=build_info["rpms"],
                    prep_artifacts_dir=prep_artifacts_dir,
                    build_artifacts_dir=build_artifacts_dir,
                )
            except BuildError as e:
                raise SignError from e


PLUGINS = [RPMSignPlugin]
