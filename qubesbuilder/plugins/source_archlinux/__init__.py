# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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

from qubesbuilder.common import is_filename_valid
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import ArchlinuxDistributionPlugin, PluginDependency
from qubesbuilder.plugins.source import SourcePlugin, SourceError


class ArchLinuxSourcePlugin(ArchlinuxDistributionPlugin, SourcePlugin):
    """
    Manage Archlinux distribution source.
    """

    name = "source_archlinux"
    stages = ["prep"]

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

        self.dependencies.append(PluginDependency("source"))

        self.environment.update({"DIST": self.dist.name, "LC_ALL": "C"})

    def run(self, **kwargs):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("prep"):
            return

        parameters = self.get_parameters(self.stage)

        # Check if we have Archlinux related content defined
        if not parameters.get("build", []):
            self.log.info(f"{self.component}:{self.dist}: Nothing to be done.")
            return

        # Compare previous artifacts hash with current source hash
        if all(
            self.component.get_source_hash()
            == self.get_dist_artifacts_info(self.stage, build.mangle()).get(
                "source-hash", None
            )
            for build in parameters["build"]
        ):
            self.log.info(
                f"{self.component}:{self.dist}: Source hash is the same than already prepared source. Skipping."
            )
            return

        # Get fetch info
        fetch_info = self.get_dist_artifacts_info(
            "fetch",
            "source",
            artifacts_dir=self.get_component_artifacts_dir("fetch"),
        )

        artifacts_dir = self.get_dist_component_artifacts_dir(self.stage)

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        for build in parameters["build"]:
            # Temporary dir for temporary copied-out files
            temp_dir = Path(tempfile.mkdtemp())

            # Source component directory inside executors
            source_dir = self.executor.get_builder_dir() / self.component.name

            # spec file basename will be used as prefix for some artifacts
            build_bn = build.mangle()

            if not (self.component.source_dir / build / "PKGBUILD.in").exists():
                msg = f"{self.component}:{self.dist}:{build}: Cannot find PKGBUILD.in."
                raise SourceError(msg)

            # Generate packages list
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (self.component.source_dir, self.executor.get_builder_dir()),
            ]

            copy_out = [
                (source_dir / f"{build_bn}_packages.list", temp_dir),
                (source_dir / f"{build_bn}_package_arch", temp_dir),
            ]

            cmd = [
                f"{self.executor.get_plugins_dir()}/source_archlinux/scripts/get-source-info "
                f"{source_dir} {source_dir / build}"
            ]
            try:
                self.executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to get source information: {str(e)}."
                raise SourceError(msg) from e

            # Read package names
            pkgnames = []
            with open(temp_dir / f"{build_bn}_packages.list") as f:
                data = f.read().splitlines()
            for line in data:
                if not is_filename_valid(line):
                    msg = f"{self.component}:{self.dist}:{build}: Invalid package name."
                    raise SourceError(msg)
                pkgnames.append(line)

            if not pkgnames:
                msg = f"{self.component}:{self.dist}:{build}: No package names defined."
                raise SourceError(msg)

            # Read architecture
            with open(temp_dir / f"{build_bn}_package_arch") as f:
                package_arch = f.read().splitlines()[0]

            # See https://wiki.archlinux.org/title/PKGBUILD#arch
            if package_arch not in ("any", "x86_64"):
                raise SourceError("Invalid architecture value.")

            # Source archive name is based on first package name
            source_orig = (
                f"{pkgnames[0]}-{self.component.get_version_release()}.tar.gz"
            )

            # Create packages list
            packages_list = []
            for pkgname in pkgnames:
                pkg = f"{pkgname}-{self.component.get_version_release()}-{package_arch}.pkg.tar.zst"
                packages_list.append(pkg)

            # Create source archive
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [(self.component.source_dir, self.executor.get_builder_dir())]

            copy_out = [
                (source_dir / "PKGBUILD", artifacts_dir),
            ]

            if self.config.increment_devel_versions:
                release = f"{self.component.release}.{self.component.devel}"
            else:
                release = self.component.release

            cmd = []

            # Create archive only if no external files are provided or if explicitly requested.
            create_archive = not parameters.get("files", [])
            create_archive = parameters.get("create-archive", create_archive)
            if create_archive:
                cmd.append(
                    f"{self.executor.get_plugins_dir()}/fetch/scripts/create-archive {source_dir} {source_orig}",
                )
                copy_out.append(
                    (source_dir / source_orig, artifacts_dir),
                )

            cmd.append(
                f"{self.executor.get_plugins_dir()}/source_archlinux/scripts/generate-pkgbuild {source_dir}/{build}/PKGBUILD.in {source_dir}/PKGBUILD {self.component.version} {release}",
            )
            try:
                self.executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to generate source: {str(e)}."
                raise SourceError(msg) from e

            # Save package information we parsed for next stages
            try:
                info = fetch_info
                info.update(
                    {
                        "packages": packages_list,
                        "source-hash": self.component.get_source_hash(),
                        "files": ["PKGBUILD"],
                    }
                )
                if create_archive:
                    info["source-archive"] = source_orig
                    info["files"].append(source_orig)

                self.save_dist_artifacts_info(
                    stage=self.stage, basename=build_bn, info=info
                )

                # Clean temporary directory
                shutil.rmtree(temp_dir)
            except OSError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to clean artifacts: {str(e)}."
                raise SourceError(msg) from e


PLUGINS = [ArchLinuxSourcePlugin]
