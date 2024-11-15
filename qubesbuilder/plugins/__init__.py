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
from pathlib import Path, PurePosixPath
from typing import List, Dict, Any

import dateutil.parser
import yaml
from dateutil.parser import parse as parsedate

from qubesbuilder.component import QubesComponent
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.exc import QubesBuilderError
from qubesbuilder.executors import Executor
from qubesbuilder.log import QubesBuilderLogger
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.template import QubesTemplate


class PackagePath(PurePosixPath):
    def mangle(self):
        return str(self).replace("/", "_")


class Dependency:
    def __init__(self, name, builder_object):
        self.name = name

        if builder_object not in ["plugin", "component"]:
            raise QubesBuilderError(
                f"Unsupported dependency type '{builder_object}'."
            )
        self.builder_object = builder_object


class PluginDependency(Dependency):
    def __init__(self, name):
        super().__init__(name=name, builder_object="plugin")


class ComponentDependency(Dependency):
    def __init__(self, name):
        super().__init__(name=name, builder_object="component")


class PluginError(QubesBuilderError):
    """
    Base plugin exception
    """

    def __init__(self, *args, additional_info=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.additional_info = additional_info or {}


class Plugin:
    """
    Generic plugin
    """

    name = "_undefined_"
    stages: List[str] = []
    priority: int = 10
    dependencies: List[Dependency] = []

    @classmethod
    def from_args(cls, **kwargs):
        return []

    def __init__(self, config, manager, **kwargs):
        # Qubes builder config
        self.config = config

        # Plugin manager
        self.manager = manager

        # Default placeholders
        self._placeholders: Dict[str, Any] = {}

        # Plugin parameters
        self._parameters: Dict[str, Any] = {}

        # Executors
        self._executors: Dict[str, Executor] = {}

        # Environment
        self.environment = {}
        if self.config.verbose:
            self.environment["VERBOSE"] = "1"
        if self.config.debug:
            self.environment["DEBUG"] = "1"
        self.environment["BACKEND_VMM"] = self.config.backend_vmm

        self.log = QubesBuilderLogger.getChild(self.name, self)

    def check_dependencies(self):
        for dependency in self.dependencies:
            if (
                dependency.builder_object == "plugin"
                and not self.manager.entities.get(dependency.name, None)
            ):
                raise PluginError(f"Cannot find plugin '{dependency}'.")
            if dependency.builder_object == "component":
                component = self.config.get_components(
                    filtered_components=[dependency.name]
                )
                if not component:
                    raise PluginError(
                        f"Cannot find component '{dependency}' in configuration file."
                    )
                if not self.config.sources_dir / dependency.name:
                    raise PluginError(
                        f"Cannot find source component '{dependency.name}' in artifacts."
                        f"Is package fetch stage done for '{dependency.name}'"
                    )
                self.log.info(
                    f"dependency '{dependency.name}' (commit hash: {component[0].get_source_commit_hash()})"
                )

    def run(self, stage: str):
        log_file = self.log.get_log_file()
        if log_file:
            self.log.info(f"Log file: {log_file}")
        self.check_dependencies()

    def update_parameters(self, stage: str):
        self._parameters.setdefault(stage, {})

    def update_placeholders(self, stage: str):
        self._placeholders.setdefault(stage, {})
        self._placeholders[stage].update(
            self.get_executor_from_config(stage).get_placeholders()
        )

    def get_executor_from_config(self, stage: str):
        if not self._executors.get(stage, None):
            self._executors[stage] = self.config.get_executor_from_config(
                stage, self
            )
        return self._executors[stage]

    def get_placeholders(self, stage: str):
        self.update_placeholders(stage)
        return self._placeholders[stage]

    def get_parameters(self, stage: str):
        self.update_parameters(stage)
        return self._parameters[stage]

    def get_cache_dir(self):
        path = self.config.artifacts_dir / "cache"
        return path.resolve()

    def get_sources_dir(self):
        path = self.config.artifacts_dir / "sources"
        return path.resolve()

    def get_repository_dir(self):
        path = self.config.artifacts_dir / "repository"
        return path.resolve()

    def get_repository_publish_dir(self):
        path = self.config.artifacts_dir / "repository-publish"
        return path.resolve()

    def get_distfiles_dir(self):
        path = self.config.artifacts_dir / "distfiles"
        return path.resolve()

    def get_templates_dir(self):
        path = self.config.artifacts_dir / "templates"
        return path.resolve()

    def get_installer_dir(self):
        path = self.config.artifacts_dir / "installer"
        return path.resolve()

    def get_iso_dir(self):
        path = self.config.artifacts_dir / "iso"
        return path.resolve()

    @staticmethod
    def get_artifacts_info_filename(stage: str, basename: str):
        return f"{basename}.{stage}.yml"

    def get_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Path
    ) -> Dict:
        fileinfo = artifacts_dir / self.get_artifacts_info_filename(
            stage, basename
        )
        if fileinfo.exists():
            try:
                with open(fileinfo, "r") as f:
                    artifacts_info = yaml.safe_load(f.read())
                return artifacts_info or {}
            except (PermissionError, yaml.YAMLError) as e:
                msg = f"{basename}: Failed to read info from {stage} stage."
                raise PluginError(msg) from e
        return {}

    def default_copy_in(self, plugins_dir, sources_dir):
        copy_in = [(self.manager.entities[self.name].directory, plugins_dir)]

        for dependency in self.dependencies:
            if dependency.builder_object == "plugin":
                copy_in += [
                    (
                        self.manager.entities[dependency.name].directory,
                        plugins_dir,
                    )
                ]
            if dependency.builder_object == "component":
                copy_in += [
                    (self.config.sources_dir / dependency.name, sources_dir)
                ]
        return copy_in


class ComponentPlugin(Plugin):
    """
    Component plugin
    """

    @classmethod
    def from_args(cls, **kwargs):
        instances = []
        if kwargs.get("stage") in cls.stages:
            for component in kwargs.get("components", []):
                instances.append(cls(component=component, **kwargs))
        return instances

    def __init__(
        self,
        component: QubesComponent,
        config,
        manager: PluginManager,
        **kwargs,
    ):
        self.component = component
        super().__init__(config=config, manager=manager, **kwargs)
        self._source_hash = ""

    def update_placeholders(self, stage: str):
        super().update_placeholders(stage)
        self._placeholders[stage].update(
            {
                "@SOURCE_DIR@": self.get_executor_from_config(
                    stage
                ).get_builder_dir()
                / self.component.name,
                "@BACKEND_VMM@": self.config.backend_vmm,
            }
        )

    def get_component_distfiles_dir(self):
        path = self.config.distfiles_dir / self.component.name
        return path

    def get_component_artifacts_dir(self, stage: str):
        path = (
            self.config.artifacts_dir
            / "components"
            / self.component.name
            / self.component.get_version_release()
            / "nodist"
            / stage
        )
        return path.resolve()

    def get_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Path = None
    ) -> Dict:
        a_dir: Path = artifacts_dir or self.get_component_artifacts_dir(stage)
        return super().get_artifacts_info(stage, basename, a_dir)

    def save_artifacts_info(
        self, stage: str, basename: str, info: dict, artifacts_dir: Path = None
    ):
        artifacts_dir = artifacts_dir or self.get_component_artifacts_dir(stage)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(
                artifacts_dir
                / self.get_artifacts_info_filename(stage, basename),
                "w",
            ) as f:
                f.write(yaml.safe_dump(info))
        except (PermissionError, yaml.YAMLError) as e:
            msg = f"{self.component}:{basename}: Failed to write info for {stage} stage."
            raise PluginError(msg) from e

    def delete_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Path = None
    ):
        artifacts_dir = artifacts_dir or self.get_component_artifacts_dir(stage)
        info_path = artifacts_dir / self.get_artifacts_info_filename(
            stage, basename
        )
        if info_path.exists():
            info_path.unlink()

    def check_stage_artifacts(self, stage: str, artifacts_dir: Path = None):
        for build in self.get_parameters(stage).get("build", []):
            build_bn = build.mangle()
            if not self.get_artifacts_info(
                stage=stage, basename=build_bn, artifacts_dir=artifacts_dir
            ):
                msg = f"Missing '{stage}' stage artifacts for {build_bn}!"
                raise PluginError(msg)


class DistributionPlugin(Plugin):
    def __init__(self, dist, config, manager, **kwargs):
        self.dist = dist
        super().__init__(config=config, manager=manager, **kwargs)

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        raise NotImplementedError

    @classmethod
    def from_args(cls, **kwargs):
        instances = []
        if kwargs.get("stage", None) in cls.stages:
            for dist in kwargs.get("distributions", []):
                if not cls.supported_distribution(dist):
                    continue
                instances.append(cls(dist=dist, **kwargs))
        return instances


def get_stage_options(stage: str, options: dict):
    stages = options.get("stages", [])
    s = next((s for s in stages if stage in s), {})
    return s.get(stage, {})


class DistributionComponentPlugin(DistributionPlugin, ComponentPlugin):
    """
    Distribution Component plugin

    Entry points:
        - PACKAGE-SET
        - PACKAGE_SET-DISTRIBUTION_NAME
    """

    @classmethod
    def from_args(cls, **kwargs):
        instances = []
        if kwargs.get("stage", None) in cls.stages:
            for component in kwargs.get("components", []):
                for dist in kwargs.get("distributions", []):
                    if not cls.supported_distribution(dist):
                        continue
                    instances.append(
                        cls(component=component, dist=dist, **kwargs)
                    )
        return instances

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config,
        manager: PluginManager,
    ):
        super().__init__(
            dist=dist, component=component, config=config, manager=manager
        )

    def update_parameters(self, stage: str):
        super().update_parameters(stage)

        parameters = self.component.get_parameters(self.get_placeholders(stage))

        # host/vm -> rpm/deb/archlinux
        self._parameters[stage].update(
            parameters.get(self.dist.package_set, {}).get(self.dist.type, {})
        )
        # host/vm -> fedora/debian/ubuntu/archlinux
        self._parameters[stage].update(
            parameters.get(self.dist.package_set, {}).get(
                self.dist.fullname, {}
            )
        )
        # Per distribution (e.g. host-fc42) overrides per package set (e.g. host)
        self._parameters[stage].update(
            parameters.get(self.dist.distribution, {}).get(self.dist.type, {})
        )

        self._parameters[stage]["build"] = [
            PackagePath(build)
            for build in self._parameters[stage].get("build", [])
        ]
        # Check conflicts when mangle paths
        mangle_builds = [
            build.mangle() for build in self._parameters[stage].get("build", [])
        ]
        if len(set(mangle_builds)) != len(self._parameters[stage]["build"]):
            raise PluginError(
                f"{self.component}:{self.dist}: Conflicting build paths"
            )

    def get_config_stage_options(self, stage: str):
        stage_options = {}
        stage_options.update(get_stage_options(stage, self.dist.kwargs))
        stage_options.update(get_stage_options(stage, self.component.kwargs))
        return stage_options

    def get_dist_component_artifacts_dir_history(self, stage: str):
        path = (
            self.config.artifacts_dir / "components" / self.component.name
        ).resolve()
        return list(path.glob(f"*/{self.dist.distribution}/{stage}"))

    def get_dist_component_artifacts_dir(self, stage: str):
        path = (
            self.config.artifacts_dir
            / "components"
            / self.component.name
            / self.component.get_version_release()
            / self.dist.distribution
            / stage
        )
        return path.resolve()

    def get_dist_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Path = None
    ) -> Dict:
        return self.get_artifacts_info(
            stage=stage,
            basename=basename,
            artifacts_dir=artifacts_dir
            or self.get_dist_component_artifacts_dir(stage),
        )

    def save_dist_artifacts_info(
        self, stage: str, basename: str, info: dict, artifacts_dir: Path = None
    ):
        return self.save_artifacts_info(
            stage=stage,
            basename=basename,
            artifacts_dir=artifacts_dir
            or self.get_dist_component_artifacts_dir(stage),
            info=info,
        )

    def delete_dist_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Path = None
    ):
        return self.delete_artifacts_info(
            stage=stage,
            basename=basename,
            artifacts_dir=artifacts_dir
            or self.get_dist_component_artifacts_dir(stage),
        )

    def check_dist_stage_artifacts(
        self, stage: str, artifacts_dir: Path = None
    ):
        return self.check_stage_artifacts(
            stage=stage,
            artifacts_dir=artifacts_dir
            or self.get_dist_component_artifacts_dir(stage),
        )

    def has_component_packages(self, stage: str):
        self.update_parameters(stage=stage)
        return self.component.has_packages and self.get_parameters(
            stage=stage
        ).get("build", [])


class TemplatePlugin(DistributionPlugin):
    def __init__(self, template: QubesTemplate, config, manager: PluginManager):
        self.template = template
        super().__init__(
            config=config, manager=manager, dist=template.distribution
        )

    @classmethod
    def supported_template(cls, template: QubesTemplate):
        raise NotImplementedError

    @classmethod
    def from_args(cls, templates, **kwargs):
        instances = []
        for template in templates:
            if not cls.supported_template(template):
                continue
            instances.append(cls(template=template, **kwargs))
        return instances

    def get_template_artifacts_info(self, stage: str) -> Dict:
        fileinfo = (
            self.config.templates_dir / f"{self.template.name}.{stage}.yml"
        )
        if fileinfo.exists():
            try:
                with open(fileinfo, "r") as f:
                    artifacts_info = yaml.safe_load(f.read())
                return artifacts_info or {}
            except (PermissionError, yaml.YAMLError) as e:
                msg = (
                    f"{self.template}: Failed to read info from {stage} stage."
                )
                raise PluginError(msg) from e
        return {}

    def save_artifacts_info(self, stage: str, info: dict):
        artifacts_dir = self.config.templates_dir
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(artifacts_dir / f"{self.template}.{stage}.yml", "w") as f:
                f.write(yaml.safe_dump(info))
        except (PermissionError, yaml.YAMLError) as e:
            msg = f"{self.template}: Failed to write info for {stage} stage."
            raise PluginError(msg) from e

    def delete_artifacts_info(self, stage: str):
        artifacts_dir = self.config.templates_dir
        info_path = artifacts_dir / f"{self.template}.{stage}.yml"
        if info_path.exists():
            info_path.unlink()

    def get_template_timestamp(self, stage="build") -> str:
        if not self.template.timestamp:
            # Read information from build stage. We need info from 'prep' stage
            # only when retrying timestamp information in 'build' stage if the
            # build occurs in two calls instead of one.
            info = self.get_template_artifacts_info(stage)
            if not info.get("timestamp", None):
                raise PluginError(
                    f"{self.template}: Cannot determine template timestamp. Missing '{stage}' stage?"
                )
            try:
                self.template.timestamp = parsedate(info["timestamp"]).strftime(
                    "%Y%m%d%H%M"
                )
            except (dateutil.parser.ParserError, IndexError) as e:
                msg = (
                    f"{self.template}: Failed to parse build timestamp format."
                )
                raise PluginError(msg) from e
        return self.template.timestamp


class RPMDistributionPlugin(DistributionPlugin):
    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_rpm()


class DEBDistributionPlugin(DistributionPlugin):
    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_deb() or distribution.is_ubuntu()


class ArchlinuxDistributionPlugin(DistributionPlugin):
    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_archlinux()


class GentooDistributionPlugin(DistributionPlugin):
    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_gentoo()

class WindowsDistributionPlugin(DistributionPlugin):
    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_windows()
