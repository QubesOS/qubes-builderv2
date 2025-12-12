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

import shutil
import tempfile
from pathlib import Path

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import ArchlinuxDistributionPlugin, PluginDependency
from qubesbuilder.plugins.sign import SignPlugin, SignError


class ArchlinuxSignPlugin(ArchlinuxDistributionPlugin, SignPlugin):
    """
    ArchlinuxBuildPlugin manages Archlinux distribution sign.

    Stages:
        - sign - Sign Archlinux package files.

    Entry points:
        - build
    """

    name = "sign_archlinux"
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
        ) or self.config.sign_key.get("archlinux", None)
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

            if not build_info.get("packages", None):
                self.log.info(
                    f"{self.component}:{self.dist}:{directory}: Nothing to sign."
                )
                continue

            packages_list = [
                build_artifacts_dir / "pkgs" / pkg
                for pkg in build_info["packages"]
            ]

            try:
                for pkg in packages_list:
                    self.log.info(
                        f"{self.component}:{self.dist}:{directory}: Signing '{pkg.name}'."
                    )
                    cmd = [
                        f"{self.config.gpg_client} --batch --no-tty --yes --detach-sign -u {sign_key} {pkg} > {pkg}.sig",
                    ]
                    self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{directory}: Failed to sign PKGs."
                raise SignError(msg) from e


PLUGINS = [ArchlinuxSignPlugin]
