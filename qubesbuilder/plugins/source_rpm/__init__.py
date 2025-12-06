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

import os
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
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.plugins import RPMDistributionPlugin, PluginDependency
from qubesbuilder.plugins.source import SourcePlugin, SourceError


class RPMSourcePlugin(RPMDistributionPlugin, SourcePlugin):
    """
    RPMSourcePlugin manages RPM distribution source.

    Stages:
        - prep: Prepare and generate source RPM.

    Entry points:
        - source
    """

    name = "source_rpm"
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

        self.dependencies += [
            PluginDependency("source"),
            PluginDependency("chroot_rpm"),
        ]

        # Add some environment variables needed to render mock root configuration
        # FIXME: Review legacy usage of "dom0" in components.
        #  "host" is aliased as "dom0" for legacy reason (it also applies in "build_rpm" plugin).
        self.environment.update(
            {
                "DIST": self.dist.name,
                "PACKAGE_SET": (
                    self.dist.package_set.replace("host", "dom0")
                    if str(self.config.use_qubes_repo.get("version", None))
                    == "4.1"
                    else self.dist.package_set
                ),
            }
        )

    def run(self, **kwargs):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run()

        if not self.has_component_packages("prep"):
            return

        parameters = self.get_parameters(self.stage)

        # Check if we have RPM related content defined
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

        artifacts_dir = self.get_dist_component_artifacts_dir(self.stage)
        distfiles_dir = self.get_component_distfiles_dir()

        # Get fetch info
        fetch_info = self.get_dist_artifacts_info(
            "fetch",
            "source",
            artifacts_dir=self.get_component_artifacts_dir("fetch"),
        )

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        # Create archive only if no external files are provided or if explicitly requested.
        create_archive = not parameters.get("files", [])
        create_archive = parameters.get("create-archive", create_archive)

        for build in parameters["build"]:
            # Temporary dir for temporary copied-out files
            temp_dir = Path(tempfile.mkdtemp())

            # Source component directory inside executors
            source_dir = self.executor.get_builder_dir() / self.component.name

            # spec file basename will be used as prefix for some artifacts
            build_bn = build.mangle()

            # generate expected artifacts info filename for sanity checks
            artifacts_info_filename = self.get_artifacts_info_filename(
                self.stage, build_bn
            )

            # Generate %{name}-%{version}-%{release} and %Source0
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (self.component.source_dir, self.executor.get_builder_dir()),
            ]

            copy_out = [
                (source_dir / f"{build_bn}_package_release_name", temp_dir),
                (source_dir / f"{build_bn}_packages.list", temp_dir),
            ]

            if self.config.increment_devel_versions:
                dist_tag = f"{self.component.devel}.{self.dist.tag}"
            else:
                dist_tag = self.dist.tag

            cmd = [
                f"{self.executor.get_plugins_dir()}/source_rpm/scripts/get-source-info "
                f"{source_dir} {source_dir / build} {dist_tag}"
            ]
            try:
                self.executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to get source information: {str(e)}."
                raise SourceError(msg) from e

            # Read package release name
            with open(temp_dir / f"{build_bn}_package_release_name") as f:
                data = f.read().splitlines()
            if len(data) < 2:
                msg = f"{self.component}:{self.dist}:{build}: Invalid data."
                raise SourceError(msg)

            source_rpm = f"{data[0]}.src.rpm"
            if not is_filename_valid(
                source_rpm, forbidden_filename=artifacts_info_filename
            ):
                msg = f"{self.component}:{self.dist}:{build}: Invalid source rpm name."
                raise SourceError(msg)

            source_orig = None
            if create_archive:
                # Source0 may contain a URL.
                source_orig = os.path.basename(data[1])
                if not is_filename_valid(
                    source_orig, forbidden_filename=artifacts_info_filename
                ):
                    msg = f"{self.component}:{self.dist}:{build}: Invalid source names."
                    raise SourceError(msg)

            # Read packages list
            packages_list = []
            with open(temp_dir / f"{build_bn}_packages.list") as f:
                data = f.read().splitlines()
            for line in data:
                if not is_filename_valid(line, allowed_ext=[".rpm"]):
                    msg = f"{self.component}:{self.dist}:{build}: Invalid package name."
                    raise SourceError(msg)
                packages_list.append(line)

            #
            # Create source RPM
            #

            # Copy-in distfiles, content and source
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (distfiles_dir, self.executor.get_distfiles_dir()),
                (self.component.source_dir, self.executor.get_builder_dir()),
            ]

            # Copy-out source RPM
            copy_out = [
                (self.executor.get_build_dir() / source_rpm, artifacts_dir),
            ]

            # Init command with .qubesbuilder command entries and then run 'mock' to generate source RPM
            cmd = parameters.get("source", {}).get("commands", [])

            mock_conf = f"{self.dist.fullname}-{self.dist.version}-{self.dist.architecture}.cfg"

            # Add prepared chroot cache
            chroot_cache_topdir = (
                self.config.cache_dir / "chroot" / self.dist.distribution
            )
            chroot_cache = chroot_cache_topdir / mock_conf.replace(".cfg", "")
            if chroot_cache.exists():
                copy_in += [
                    (chroot_cache, self.executor.get_cache_dir() / "mock")
                ]
                cmd += [
                    f"sudo chown -R root:mock {self.executor.get_cache_dir() / 'mock'}"
                ]

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
            if create_archive:
                # If no Source0 is provided, we expect 'source' from query-spec.
                if source_orig != "source":
                    cmd += [
                        f"{self.executor.get_plugins_dir()}/fetch/scripts/create-archive {source_dir} {source_orig}",
                    ]

            for file in parameters.get("files", []):
                fn = get_archive_name(file)
                cmd.append(
                    f"mv {self.executor.get_distfiles_dir() / self.component.name / fn} {source_dir}"
                )
                if file.get("signature", None):
                    cmd.append(
                        f"mv {self.executor.get_distfiles_dir() / self.component.name / os.path.basename(file['signature'])} {source_dir}"
                    )

            for module in fetch_info.get("modules", []):
                cmd.append(
                    f"mv {self.executor.get_distfiles_dir() / self.component.name / module['archive']} {source_dir}"
                )
                cmd.append(
                    f"sed -i 's/@{module['name']}@/{module['archive']}/g' {source_dir / build}.in"
                )

            # Generate the spec that Mock will use for creating source RPM ensure 'mock'
            # group can access build directory
            cmd += [
                f"{self.executor.get_plugins_dir()}/source_rpm/scripts/generate-spec {source_dir} {source_dir / build}.in {source_dir / build}",
                f"mkdir -p {self.executor.get_build_dir()}",
                f"sudo chown -R {self.executor.get_user()}:mock {self.executor.get_build_dir()}",
            ]

            mock_cmd = [
                f"sudo --preserve-env=DIST,PACKAGE_SET,USE_QUBES_REPO_VERSION",
                f"/usr/libexec/mock/mock",
                "--verbose",
                "--buildsrpm",
                f"--spec {source_dir / build}",
                f"--root {self.executor.get_plugins_dir()}/chroot_rpm/mock/{mock_conf}",
                f"--sources={source_dir}",
                f"--resultdir={self.executor.get_build_dir()}",
                "--disablerepo=builder-local",
            ]
            if isinstance(self.executor, ContainerExecutor):
                msg = f"{self.component}:{self.dist}:{build}: Mock isolation set to 'simple', build has full network access. Use 'qubes' executor for network-isolated build."
                self.log.warning(msg)
                mock_cmd.append("--isolation=simple")
            else:
                mock_cmd.append("--isolation=nspawn")
            if chroot_cache.exists():
                mock_cmd.append("--plugin-option=root_cache:age_check=False")
            if self.config.increment_devel_versions:
                mock_cmd.append(f"--define 'dist .{dist_tag}'")
            if chroot_cache.exists():
                mock_cmd.append("--no-clean")

            files_inside_executor_with_placeholders = [
                f"@PLUGINS_DIR@/chroot_rpm/mock/{mock_conf}"
            ]

            cmd += [" ".join(mock_cmd)]
            try:
                self.executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                    files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to generate SRPM ({str(e)})"
                errors, start_line = extract_lines_before(
                    self.log.get_log_file(), "EXCEPTION:.*/usr/bin/rpmbuild -bs"
                )
                additional_info = {
                    "log_file": self.log.get_log_file().name,
                    "start_line": start_line,
                    "lines": errors,
                }
                raise SourceError(msg, additional_info=additional_info) from e

            # Save package information we parsed for next stages
            try:
                info = fetch_info
                info.update(
                    {
                        "files": [source_rpm],
                        "srpm": source_rpm,
                        "rpms": packages_list,
                        "source-hash": self.component.get_source_hash(),
                    }
                )
                self.save_dist_artifacts_info(
                    stage=self.stage, basename=build_bn, info=info
                )

                # Clean temporary directory
                shutil.rmtree(temp_dir)
            except OSError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to clean artifacts: {str(e)}."
                raise SourceError(msg) from e


PLUGINS = [RPMSourcePlugin]
