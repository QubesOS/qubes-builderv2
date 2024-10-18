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

import logging
import os
import shutil
from pathlib import Path
from typing import List

from qubesbuilder.common import extract_lines_before

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.plugins import DEBDistributionPlugin, PluginDependency
from qubesbuilder.plugins.build import BuildPlugin, BuildError


def provision_local_repository(
    log: logging.Logger,
    debian_directory: str,
    repository_dir: Path,
    component: QubesComponent,
    dist: QubesDistribution,
    source_info: dict,
    packages_list: List,
    build_artifacts_dir: Path,
):
    """
    Provision local builder repository.
    """
    log.info(
        f"{component}:{dist}:{debian_directory}: Provisioning local repository '{repository_dir}'."
    )

    # Create target directory that will have hardlinks to built packages
    target_dir = repository_dir / f"{component.name}_{component.version}"
    if target_dir.exists():
        shutil.rmtree(target_dir.as_posix())
    target_dir.mkdir(parents=True)

    try:
        debian_source_files = ["dsc", "debian"]
        if source_info["package-type"] == "quilt":
            debian_source_files.append("orig")

        # deb
        files = [build_artifacts_dir / deb for deb in packages_list]
        files += [
            build_artifacts_dir / source_info[f] for f in debian_source_files
        ]

        # changes and buildinfo
        files += [
            build_artifacts_dir / source_info["changes"],
            build_artifacts_dir / source_info["buildinfo"],
        ]

        # create hardlinks
        for f in files:
            target_path = target_dir / f.name
            # target_path.hardlink_to(f)
            os.link(f, target_path)
    except (ValueError, PermissionError, NotImplementedError) as e:
        msg = f"{component}:{dist}:{debian_directory}: Failed to create repository."
        raise BuildError(msg) from e


class DEBBuildPlugin(DEBDistributionPlugin, BuildPlugin):
    """
    DEBBuildPlugin manages Debian distribution build.

    Stages:
        - build - Build Debian packages and provision local repository.

    Entry points:
        - build
    """

    name = "build_deb"
    stages = ["build"]
    dependencies = [PluginDependency("chroot_deb"), PluginDependency("build")]

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

    def run(self, stage: str):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run(stage=stage)

        if stage != "build" or not self.has_component_packages("build"):
            return

        executor = self.get_executor_from_config(stage)
        parameters = self.get_parameters(stage)
        artifacts_dir = self.get_dist_component_artifacts_dir(stage)

        # Compare previous artifacts hash with current source hash
        if all(
            self.component.get_source_hash()
            == self.get_dist_artifacts_info(
                stage=stage, basename=directory.mangle()
            ).get("source-hash", None)
            for directory in parameters["build"]
        ):
            self.log.info(
                f"{self.component}:{self.dist}: Source hash is the same than already built source. Skipping."
            )
            return

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        # Source artifacts
        prep_artifacts_dir = self.get_dist_component_artifacts_dir(stage="prep")

        repository_dir = self.config.repository_dir / self.dist.distribution
        repository_dir.mkdir(parents=True, exist_ok=True)

        # Remove previous versions in order to keep the latest one only
        for build in repository_dir.glob(f"{self.component.name}_*"):
            shutil.rmtree(build.as_posix())

        for directory in parameters["build"]:
            # directory basename will be used as prefix for some artifacts
            directory_bn = directory.mangle()

            # Read information from source stage
            source_info = self.get_dist_artifacts_info(
                stage="prep", basename=directory_bn
            )

            #
            # Build Debian packages
            #

            debian_source_files = ["dsc", "debian"]
            if not source_info.get("package-type", None):
                raise BuildError(
                    f"Cannot determine source type. Missing 'prep' stage call?"
                )
            if source_info["package-type"] == "quilt":
                debian_source_files.append("orig")
            for src in debian_source_files:
                if not source_info.get(src, None):
                    raise BuildError(f"Cannot find sources for '{directory}'")

            # Copy-in plugin, repository and sources
            copy_in = self.default_copy_in(
                executor.get_plugins_dir(), executor.get_sources_dir()
            ) + [
                (
                    self.manager.entities["chroot_deb"].directory / "pbuilder",
                    executor.get_builder_dir(),
                ),
                (repository_dir, executor.get_repository_dir()),
            ]

            copy_in += [
                (prep_artifacts_dir / source_info[f], executor.get_build_dir())
                for f in debian_source_files
            ]

            # Files inside executor
            files_inside_executor_with_placeholders = [
                "@BUILDER_DIR@/pbuilder/pbuilderrc"
            ]

            results_dir = executor.get_builder_dir() / "pbuilder" / "results"
            source_info["changes"] = source_info["dsc"].replace(
                ".dsc", "_amd64.changes"
            )
            source_info["buildinfo"] = source_info["dsc"].replace(
                ".dsc", "_amd64.buildinfo"
            )
            copy_out = [
                (
                    results_dir / source_info["changes"],
                    artifacts_dir,
                ),
                (
                    results_dir / source_info["buildinfo"],
                    artifacts_dir,
                ),
            ]
            # We prefer to keep originally copied in source to cross-check
            # what's inside the resulting .changes file.
            # copy_out += [
            #     (results_dir / source_info[f], artifacts_dir) for f in debian_source_files
            # ]
            copy_out += [
                (results_dir / deb, artifacts_dir)
                for deb in source_info["packages"]
            ]

            # Create local builder repository
            extra_sources = (
                f"deb [trusted=yes] file:///tmp/qubes-deb {self.dist.name} main"
            )
            # extra_sources = ""

            cmd = [
                f"mkdir -p {executor.get_cache_dir()}/aptcache",
                f"{executor.get_plugins_dir()}/build_deb/scripts/create-local-repo {executor.get_repository_dir()} {self.dist.fullname} {self.dist.name}",
            ]

            # If provided, use the first mirror given in builder configuration mirrors list
            mirrors = self.config.get("mirrors", {}).get(self.dist.fullname, [])
            if mirrors:
                cmd += [
                    f"sed -i 's@MIRRORSITE=https://deb.debian.org/debian@MIRRORSITE={mirrors[0]}@' {executor.get_builder_dir()}/pbuilder/pbuilderrc"
                ]

            if self.config.use_qubes_repo.get("version", None):
                repo_server = (
                    "debu.qubes-os.org"
                    if self.dist.fullname == "ubuntu"
                    else "deb.qubes-os.org"
                )
                qubes_version = self.config.use_qubes_repo["version"]
                extra_sources = f"{extra_sources}|deb [arch=amd64] https://{repo_server}/r{qubes_version}/vm {self.dist.name} main"
                keyring_file = (
                    f"qubes-{self.dist.fullname}-r{qubes_version}.asc"
                )
                cmd += [
                    f"gpg --dearmor "
                    f"< {executor.get_plugins_dir()}/chroot_deb/keys/{keyring_file} "
                    f"> {executor.get_builder_dir()}/pbuilder/qubes-keyring.gpg"
                ]
                if self.config.use_qubes_repo.get("testing", False):
                    extra_sources = f"{extra_sources}|deb [arch=amd64] https://{repo_server}/r{qubes_version}/vm {self.dist.name}-testing main"

            # fmt: off
            # FIXME: We disable black here because it removes escaped quotes.
            #  This is until we use shlex.quote.

            # Add downloaded packages and prepared chroot cache
            chroot_dir = self.config.cache_dir / "chroot" / self.dist.name
            aptcache_dir = chroot_dir / "pbuilder/aptcache"
            base_tgz = chroot_dir / "pbuilder/base.tgz"
            if aptcache_dir.exists():
                copy_in += [(
                    chroot_dir / "pbuilder/aptcache",
                    executor.get_cache_dir(),
                )]
            if base_tgz.exists():
                copy_in += [
                    (base_tgz, executor.get_builder_dir() / "pbuilder")
                ]
                cmd += [
                    f"sudo -E pbuilder update "
                    f"--distribution {self.dist.name} "
                    f"--configfile {executor.get_builder_dir()}/pbuilder/pbuilderrc "
                    f"--othermirror \"{extra_sources}\""
                ]
            else:
                cmd += [
                    f"sudo -E pbuilder create "
                    f"--distribution {self.dist.name} "
                    f"--configfile {executor.get_builder_dir()}/pbuilder/pbuilderrc "
                    f"--othermirror \"{extra_sources}\""
                ]

            cmd += [
                f"sudo -E pbuilder build --override-config "
                f"--distribution {self.dist.name} "
                f"--configfile {executor.get_builder_dir()}/pbuilder/pbuilderrc "
                f"--othermirror \"{extra_sources}\" "
                f"{str(executor.get_build_dir() / source_info['dsc'])}"
            ]

            # Patch .changes path to have source package checksums of the original source,
            # not the one rebuilt during binary build. They should be reproducible
            # in theory, but due to dpkg-source bugs sometimes they are not.
            cmd += [
                f"{executor.get_plugins_dir()}/build_deb/scripts/patch-changes "
                f"{str(executor.get_build_dir() / source_info['dsc'])} "
                f"{str(results_dir / source_info['buildinfo'])} "
                f"{str(results_dir / source_info['changes'])}"
            ]
            # fmt: on
            try:
                executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                    no_fail_copy_out_allowed_patterns=["-dbgsym_"],
                    files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{directory}: Failed to build packages: {str(e)}"
                errors, start_line = extract_lines_before(
                    self.log.get_log_file(),
                    "dpkg-buildpackage: error:",
                    max_split=3,
                )
                additional_info = {
                    "log_file": self.log.get_log_file().name,
                    "start_line": start_line,
                    "lines": errors,
                }
                raise BuildError(msg, additional_info=additional_info) from e

            # Get packages list that have been actually built from predicted ones
            packages_list = []
            for deb in source_info["packages"]:
                if os.path.exists(artifacts_dir / deb):
                    packages_list.append(deb)

            # We prefer to keep originally copied in source to cross-check
            # what's inside the resulting .changes file.
            files = [
                prep_artifacts_dir / source_info[f] for f in debian_source_files
            ]
            for file in files:
                target_path = artifacts_dir / file.name
                shutil.copy2(file, target_path)

            # Provision builder local repository
            provision_local_repository(
                log=self.log,
                debian_directory=directory,
                component=self.component,
                dist=self.dist,
                repository_dir=repository_dir,
                source_info=source_info,
                packages_list=packages_list,
                build_artifacts_dir=artifacts_dir,
            )

            # Save package information we parsed for next stages
            info = source_info
            info["packages"] = packages_list
            info["source-hash"] = self.component.get_source_hash()
            self.save_dist_artifacts_info(
                stage=stage, basename=directory_bn, info=info
            )


PLUGINS = [DEBBuildPlugin]
