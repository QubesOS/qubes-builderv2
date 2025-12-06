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
from qubesbuilder.plugins import DEBDistributionPlugin, PluginDependency
from qubesbuilder.plugins.build import BuildError
from qubesbuilder.plugins.build_deb import provision_local_repository
from qubesbuilder.plugins.sign import SignPlugin, SignError


class DEBSignPlugin(DEBDistributionPlugin, SignPlugin):
    """
    DEBSignPlugin manages Debian distribution sign.

    Stages:
        - sign - Sign Debian package files.

    Entry points:
        - build
    """

    name = "sign_deb"
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

        self.dependencies += [
            PluginDependency("sign"),
            PluginDependency("build_deb"),
        ]

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
        ) or self.config.sign_key.get("deb", None)
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

        # Build artifacts (source included)
        build_artifacts_dir = self.get_dist_component_artifacts_dir(
            stage="build"
        )
        # Sign artifacts
        artifacts_dir = self.get_dist_component_artifacts_dir(self.stage)

        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir)
        artifacts_dir.mkdir(parents=True)

        keyring_dir = artifacts_dir / "keyring"
        keyring_dir.mkdir(mode=0o700)

        # Export public key and generate local keyring
        temp_dir = Path(tempfile.mkdtemp())
        sign_key_asc = temp_dir / f"{sign_key}.asc"
        cmd = [
            f"{self.config.gpg_client} --armor --export {sign_key} > {sign_key_asc}",
            f"gpg2 --homedir {keyring_dir} --import {sign_key_asc}",
        ]
        try:
            self.executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}: Failed to export public signing key."
            raise SignError(msg) from e
        finally:
            shutil.rmtree(temp_dir)

        for directory in parameters["build"]:
            # directory basename will be used as prefix for some artifacts
            directory_bn = directory.mangle()

            # Read information from build stage
            build_info = self.get_dist_artifacts_info(
                stage="build", basename=directory_bn
            )

            if not build_info.get("changes", None):
                self.log.info(
                    f"{self.component}:{self.dist}:{directory}: Nothing to sign."
                )
                continue
            try:
                self.log.info(
                    f"{self.component}:{self.dist}:{directory}: Signing from '{build_info['changes']}' info."
                )
                cmd = [
                    f"debsign -k{sign_key} -p{self.config.gpg_client} --no-re-sign {build_artifacts_dir / build_info['changes']}"
                ]
                self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{directory}: Failed to sign Debian packages."
                raise SignError(msg) from e

            # Re-provision builder local repository with signatures
            repository_dir = self.config.repository_dir / self.dist.distribution
            try:
                # We use build_info that contains source_info and build_artifacts_dir
                # which contains sources files.
                provision_local_repository(
                    log=self.log,
                    debian_directory=directory,
                    component=self.component,
                    dist=self.dist,
                    repository_dir=repository_dir,
                    source_info=build_info,
                    packages_list=build_info["packages"],
                    build_artifacts_dir=build_artifacts_dir,
                )
            except BuildError as e:
                msg = f"{self.component}:{self.dist}:{directory}: Failed to re-provision local repository."
                raise SignError(msg) from e


PLUGINS = [DEBSignPlugin]
