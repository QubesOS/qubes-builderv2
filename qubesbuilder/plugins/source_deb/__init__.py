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

from qubesbuilder.common import (
    is_filename_valid,
    get_archive_name,
    extract_lines_before,
)
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import DEBDistributionPlugin, PluginDependency
from qubesbuilder.plugins.source import SourcePlugin, SourceError


class DEBSourcePlugin(DEBDistributionPlugin, SourcePlugin):
    """
    DEBSourcePlugin manages Debian distribution source.

    Stages:
        - prep: Prepare and generate Debian source package (.orig.tar.*, .dsc and .debian.tar.xz).

    Entry points:
        - source
    """

    name = "source_deb"
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

        self.environment.update(
            {
                "DIST": self.dist.name,
                "LC_ALL": "C",
                "DEBFULLNAME": "Builder",
                "DEBEMAIL": "user@localhost",
            }
        )

    def run(self):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("prep"):
            return

        parameters = self.get_parameters(self.stage)

        # Check if we have Debian related content defined
        if not parameters.get("build", None):
            self.log.info(
                f"{self.component}: nothing to be done for {self.dist}"
            )
            return

        distfiles_dir = self.get_component_distfiles_dir()
        artifacts_dir = self.get_dist_component_artifacts_dir(self.stage)

        # Compare previous artifacts hash with current source hash
        if all(
            self.component.get_source_hash()
            == self.get_dist_artifacts_info(
                stage=self.stage, basename=directory.mangle()
            ).get("source-hash", None)
            for directory in parameters["build"]
        ):
            self.log.info(
                f"{self.component}:{self.dist}: Source hash is the same than already prepared source. Skipping."
            )
            return

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        # Get fetch info
        fetch_info = self.get_dist_artifacts_info(
            "fetch",
            "source",
            artifacts_dir=self.get_component_artifacts_dir("fetch"),
        )

        for directory in parameters["build"]:
            # Temporary dir for temporary copied-out files
            temp_dir = Path(tempfile.mkdtemp())

            # Source component directory inside executors
            source_dir = self.executor.get_builder_dir() / self.component.name

            # directory basename will be used as prefix for some artifacts
            directory_bn = directory.mangle()

            # generate expected artifacts info filename for sanity checks
            artifacts_info_filename = self.get_artifacts_info_filename(
                self.stage, directory_bn
            )

            # Generate package release name
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (self.component.source_dir, self.executor.get_builder_dir()),
            ]

            copy_out = [
                (source_dir / f"{directory_bn}_package_release_name", temp_dir)
            ]

            # Update changelog
            cmd = [
                f"{self.executor.get_plugins_dir()}/source_deb/scripts/modify-changelog-for-build "
                f"{source_dir} {directory} {self.dist.name} {self.dist.tag} {self.component.devel}",
            ]

            cmd += [
                f"{self.executor.get_plugins_dir()}/source_deb/scripts/get-source-info {source_dir} {directory}"
            ]
            try:
                self.executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = (
                    f"{self.component}:{self.dist}:{directory}: "
                    f"Failed to get source information: {str(e)}."
                )
                raise SourceError(msg) from e

            # Read package release name
            with open(temp_dir / f"{directory_bn}_package_release_name") as f:
                data = f.read().splitlines()
            if len(data) != 3:
                msg = f"{self.component}:{self.dist}:{directory}: Invalid data."
                raise SourceError(msg)

            package_release_name = data[0]
            package_release_name_full = data[1]
            package_type = data[2]
            if not is_filename_valid(
                package_release_name, forbidden_filename=artifacts_info_filename
            ) or not is_filename_valid(
                package_release_name_full,
                forbidden_filename=artifacts_info_filename,
            ):
                msg = f"{self.component}:{self.dist}:{directory}: Invalid source names."
                raise SourceError(msg)

            if package_type not in ("native", "quilt"):
                msg = f"{self.component}:{self.dist}:{directory}: Invalid source type."
                raise SourceError(msg)

            source_dsc = f"{package_release_name_full}.dsc"
            if package_type == "native":
                source_debian = f"{package_release_name_full}.tar.xz"
            else:
                source_debian = f"{package_release_name_full}.debian.tar.xz"
            if parameters.get("files", []):
                # FIXME: The first file is the source archive. Is it valid for all the cases?
                ext = Path(get_archive_name(parameters["files"][0])).suffix
                msg = f"{self.component}:{self.dist}:{directory}: Invalid extension '{ext}'."
                if ext not in (".gz", ".bz2", ".xz", ".lzma2"):
                    raise SourceError(msg)
            else:
                ext = ".gz"
            source_orig = f"{package_release_name}.orig.tar{ext}"

            #
            # Create Debian source: orig, debian and dsc
            #

            # Copy-in distfiles, dependencies, source and Debian directory
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (self.component.source_dir, self.executor.get_builder_dir()),
                (distfiles_dir, self.executor.get_distfiles_dir()),
            ]

            # Copy-out Debian source package (.orig.tar.*, .dsc and .debian.tar.xz)
            copy_out = [
                (self.executor.get_builder_dir() / source_dsc, artifacts_dir),
                (
                    self.executor.get_builder_dir() / source_debian,
                    artifacts_dir,
                ),
                (
                    self.executor.get_builder_dir()
                    / f"{directory_bn}_packages.list",
                    temp_dir,
                ),
            ]
            if package_type == "quilt":
                copy_out += [
                    (
                        self.executor.get_builder_dir() / source_orig,
                        artifacts_dir,
                    )
                ]

            # Init command with .qubesbuilder command entries
            cmd = parameters.get("source", {}).get("commands", [])

            if package_type == "quilt":
                if self.component.is_salt():
                    if not (
                        self.component.source_dir / "Makefile.install"
                    ).exists():
                        copy_in += [
                            (
                                self.manager.entities["source"].directory
                                / "salt/Makefile.install",
                                source_dir,
                            )
                        ]
                    cmd += [
                        f"{self.executor.get_plugins_dir()}/source/salt/yaml-dumper "
                        "--env VERBOSE "
                        f"--outfile {source_dir}/Makefile.vars -- "
                        f"{self.executor.get_plugins_dir()}/source/salt/FORMULA-DEFAULTS {source_dir}/FORMULA"
                    ]

                # Create archive only if no external files are provided or if explicitly requested.
                create_archive = not parameters.get("files", [])
                create_archive = parameters.get(
                    "create-archive", create_archive
                )
                if create_archive:
                    cmd += [
                        f"{self.executor.get_plugins_dir()}/fetch/scripts/create-archive {source_dir} {source_orig}",
                        f"mv {source_dir}/{source_orig} {self.executor.get_builder_dir()}",
                    ]
                for file in parameters.get("files", []):
                    fn = get_archive_name(file)
                    cmd.append(
                        f"mv {self.executor.get_distfiles_dir() / self.component.name / fn} {self.executor.get_builder_dir()}/{source_orig}"
                    )

            # Update changelog, after create-archive
            cmd += [
                f"{self.executor.get_plugins_dir()}/source_deb/scripts/modify-changelog-for-build "
                f"{source_dir} {directory} {self.dist.name} {self.dist.tag} {self.component.devel}",
            ]

            gen_packages_list_cmd = [
                f"{self.executor.get_plugins_dir()}/source_deb/scripts/debian-get-packages-list",
                str(self.executor.get_builder_dir() / source_dsc),
                f">{self.executor.get_builder_dir()}/{directory_bn}_packages.list",
            ]

            # Run 'dpkg-source' inside build directory
            if package_type == "quilt":
                cmd += [
                    f"mkdir -p {self.executor.get_build_dir()}",
                    f"cd {self.executor.get_build_dir()}",
                    f"cp -a {source_dir / directory} .",
                ]
            else:
                # For native package, we need to match archive prefix in order
                # to not have a different one at build stage. For example,
                # 'build/' vs 'qubes-utils_4.1.16+deb11u1/'
                build_dir = str(
                    self.executor.get_builder_dir() / package_release_name_full
                ).replace("_", "-")
                cmd += [
                    f"mkdir -p {build_dir}",
                    f"cd {build_dir}",
                    f"cp -a {source_dir}/* .",
                ]
            cmd += [
                # Workaround for https://bugs.debian.org/796257, or rather its
                # complementary part (asymmetry between extract and build)
                # Taken from Dpkg::Source::Functions::fixperms, assuming umask 022
                "chmod -R -- u+rwX,g+rX-w,o+rX-w .",
                "chmod +x debian/rules",
                "dpkg-source -b .",
                " ".join(gen_packages_list_cmd),
            ]
            try:
                self.executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{directory}: Failed to generate source: {str(e)}"
                errors, start_line = extract_lines_before(
                    self.log.get_log_file(), "dpkg-source: error:", max_split=3
                )
                additional_info = {
                    "log_file": self.log.get_log_file().name,
                    "start_line": start_line,
                    "lines": errors,
                }
                raise SourceError(msg, additional_info=additional_info) from e

            # Read packages list
            packages_list = []
            with open(temp_dir / f"{directory_bn}_packages.list") as f:
                data = f.read().splitlines()
            for line in data:
                if not is_filename_valid(
                    line, allowed_ext=[".deb", ".ddeb", ".udeb"]
                ):
                    msg = f"{self.component}:{self.dist}:{directory}: Invalid package name."
                    raise SourceError(msg)
                packages_list.append(line)

            # Save package information we parsed for next stages
            try:
                info = fetch_info
                info.update(
                    {
                        "package-release-name": package_release_name,
                        "package-release-name-full": package_release_name_full,
                        "package-type": package_type,
                        "dsc": source_dsc,
                        "debian": source_debian,
                        "packages": packages_list,
                        "source-hash": self.component.get_source_hash(),
                        "files": [source_dsc, source_debian],
                    }
                )
                if package_type == "quilt":
                    info["orig"] = source_orig
                    info["files"].append(source_orig)

                self.save_dist_artifacts_info(
                    stage=self.stage, basename=directory_bn, info=info
                )

                # Clean temporary directory
                shutil.rmtree(temp_dir)
            except OSError as e:
                msg = f"{self.component}:{self.dist}:{directory}: Failed to clean artifacts: {str(e)}."
                raise SourceError(msg) from e


PLUGINS = [DEBSourcePlugin]
