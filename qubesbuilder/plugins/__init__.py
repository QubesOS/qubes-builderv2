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
import enum
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Dict, Any, Optional, Callable

import dateutil.parser
import yaml
from dateutil.parser import parse as parsedate

from qubesbuilder.component import QubesComponent
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.exc import QubesBuilderError
from qubesbuilder.log import QubesBuilderLogger
from qubesbuilder.template import QubesTemplate


class PackagePath(PurePosixPath):
    def mangle(self):
        return str(self).replace("/", "_")


class PluginError(QubesBuilderError):
    def __init__(self, *args, additional_info=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.additional_info = additional_info or {}


@dataclass(frozen=True)
class JobReference:
    component: Optional[Any] = None
    dist: Optional[Any] = None
    template: Optional[Any] = None
    stage: Optional[str] = None
    build: Optional[str] = None

    def __repr__(self):
        parts = []
        if self.component is not None:
            parts.append(f"component={self.component.name}")
        if self.dist is not None:
            parts.append(f"dist={self.dist.distribution}")
        if self.template is not None:
            parts.append(f"template={self.template.name}")
        if self.stage is not None:
            parts.append(f"stage={self.stage}")
        if self.build is not None:
            parts.append(f"build={self.build}")
        return (
            f"<JobReference({', '.join(parts)})>"
            if parts
            else "<JobReference()>"
        )


class PluginDependency:
    def __init__(self, reference: str):
        self.reference = reference


class ComponentDependency:
    def __init__(self, reference: str):
        self.reference = reference


class JobDependency:
    def __init__(self, reference: "JobReference"):
        self.reference = reference


class PluginContext(enum.Flag):
    COMPONENT = enum.auto()
    DIST = enum.auto()
    TEMPLATE = enum.auto()


def get_relative_artifacts_path(job_ref: JobReference) -> Path:
    if job_ref.stage is None:
        raise PluginError(f"JobReference has no stage: {job_ref!r}")
    if job_ref.template:
        relative_path = Path(f"{job_ref.template.name}.{job_ref.stage}.yml")
    elif job_ref.dist and job_ref.component:
        if not job_ref.build:
            raise PluginError(
                "JobReference for DistributionComponentPlugin requires a build identifier."
            )
        basename = PackagePath(job_ref.build).mangle()
        filename = Plugin.get_artifacts_info_filename(job_ref.stage, basename)
        relative_path = (
            Path("components")
            / job_ref.component.name
            / job_ref.component.get_version_release()
            / job_ref.dist.distribution
            / job_ref.stage
            / filename
        )
    elif job_ref.component:
        if not job_ref.build:
            raise PluginError(
                "JobReference for ComponentPlugin requires a build identifier."
            )
        basename = PackagePath(job_ref.build).mangle()
        filename = Plugin.get_artifacts_info_filename(job_ref.stage, basename)
        relative_path = (
            Path("components")
            / job_ref.component.name
            / job_ref.component.get_version_release()
            / "nodist"
            / job_ref.stage
            / filename
        )
    elif job_ref.dist:
        if not job_ref.build:
            raise PluginError(
                "JobReference for DistributionPlugin requires a build identifier."
            )
        if job_ref.stage == "init-cache":
            build = PackagePath(job_ref.build).mangle()
            filename = Plugin.get_artifacts_info_filename(job_ref.stage, build)
            relative_path = (
                Path("cache")
                / "chroot"
                / job_ref.dist.distribution
                / build
                / filename
            )
        else:
            raise PluginError(
                "JobReference for non init-cache stage are not implemented."
            )
    else:
        raise PluginError(
            "Missing distribution, component or template in JobReference!"
        )
    return relative_path


def get_artifacts_path(config, job_ref: JobReference) -> Path:
    if job_ref.template:
        base_dir = config.templates_dir
    elif job_ref.component or job_ref.dist:
        base_dir = config.artifacts_dir
    else:
        raise PluginError(
            "Missing distribution, component or template in JobReference!"
        )
    return base_dir / get_relative_artifacts_path(job_ref)


def get_stage_options(stage: str, options: dict):
    stages = options.get("stages", [])
    s: dict = next((s for s in stages if stage in s), {})
    return s.get(stage, {})


class Plugin:
    name = "_undefined_"
    stages: List[str] = []
    priority: int = 10
    context: PluginContext = PluginContext(0)
    dist_filter: Optional[Callable] = None
    dependencies: List = []
    _signing_not_configured_warned = False

    @classmethod
    def matches(cls, **kwargs) -> bool:
        stage = kwargs.get("stage")
        if stage not in cls.stages:
            return False

        component = kwargs.get("component")
        dist = kwargs.get("dist")
        template = kwargs.get("template")

        if PluginContext.COMPONENT in cls.context and not component:
            return False
        if PluginContext.DIST in cls.context and not dist:
            return False
        if PluginContext.TEMPLATE in cls.context and not template:
            return False

        if component and PluginContext.COMPONENT not in cls.context:
            return False
        if dist and PluginContext.DIST not in cls.context:
            return False
        if template and PluginContext.TEMPLATE not in cls.context:
            return False

        if cls.dist_filter is not None and dist and not cls.dist_filter(dist):
            return False

        return True

    @classmethod
    def from_args(cls, **kwargs) -> Optional["Plugin"]:
        if cls.matches(**kwargs):
            init_kwargs = {"config": kwargs["config"], "stage": kwargs["stage"]}
            if kwargs.get("component") is not None:
                init_kwargs["component"] = kwargs["component"]
            if kwargs.get("dist") is not None:
                init_kwargs["dist"] = kwargs["dist"]
            if kwargs.get("template") is not None:
                init_kwargs["template"] = kwargs["template"]
            return cls(**init_kwargs)
        return None

    @classmethod
    def get_artifacts_info_filename(cls, stage: str, basename: str):
        return f"{basename}.{stage}.yml"

    @classmethod
    def is_signing_configured(cls, config, dist, component):
        sign_key = config.sign_key.get(
            dist.distribution, None
        ) or config.sign_key.get(dist.type, None)

        if not sign_key:
            if not cls._signing_not_configured_warned:
                QubesBuilderLogger.info(
                    f"{cls.name}:{dist}: No signing key found."
                )
                cls._signing_not_configured_warned = True
            return False
        if not config.gpg_client:
            if not cls._signing_not_configured_warned:
                QubesBuilderLogger.info(
                    f"{cls.name}:{dist}: Please specify GPG client to use!"
                )
                cls._signing_not_configured_warned = True
            return False
        return True

    def get_artifact_context(self) -> dict:
        return {"config": self.config}

    def __init__(
        self,
        config,
        stage: str,
        component: Optional[QubesComponent] = None,
        dist: Optional[QubesDistribution] = None,
        template: Optional[QubesTemplate] = None,
        **kwargs,
    ):
        self.config = config
        self.manager = self.config.get_plugin_manager()
        self._placeholders: Dict[str, Any] = {}
        self._parameters: Dict[str, Any] = {}
        self.environment: Dict[str, Any] = {}
        if self.config.verbose:
            self.environment["VERBOSE"] = "1"
        if self.config.debug:
            self.environment["DEBUG"] = "1"
        self.environment["BACKEND_VMM"] = self.config.backend_vmm
        self.stage = stage
        self.component = component
        self.dist = dist
        self.template = template
        if template is not None and dist is None:
            self.dist = template.distribution
        if component is not None:
            self._source_hash = ""
        self.log = QubesBuilderLogger.getChild(self.name, self)
        self.executor = self.config.get_executor_from_config(stage, self)
        self.dependencies = []

    def update_placeholders(self, stage: str):
        self._placeholders.setdefault(stage, self.executor.get_placeholders())
        if self.component:
            self._placeholders[stage].update(
                {
                    "@SOURCE_DIR@": self.executor.get_builder_dir()
                    / self.component.name,
                    "@BACKEND_VMM@": self.config.backend_vmm,
                }
            )

    def update_parameters(self, stage: str):
        self._parameters.setdefault(stage, {})
        if not self.component or not self.dist:
            return

        parameters = self.component.get_parameters(self.get_placeholders(stage))

        self._parameters[stage].update(
            parameters.get(self.dist.package_set, {}).get(self.dist.type, {})
        )
        self._parameters[stage].update(
            parameters.get(self.dist.package_set, {}).get(
                self.dist.fullname, {}
            )
        )
        self._parameters[stage].update(
            parameters.get(self.dist.distribution, {}).get(self.dist.type, {})
        )

        self._parameters[stage]["build"] = [
            PackagePath(build)
            for build in self._parameters[stage].get("build", [])
        ]
        mangle_builds = [
            build.mangle() for build in self._parameters[stage].get("build", [])
        ]
        if len(set(mangle_builds)) != len(self._parameters[stage]["build"]):
            raise PluginError(
                f"{self.component}:{self.dist}: Conflicting build paths"
            )

    def get_placeholders(self, stage: str):
        self.update_placeholders(stage)
        return self._placeholders[stage]

    def get_parameters(self, stage: str):
        self.update_parameters(stage)
        return self._parameters[stage]

    def get_config_stage_options(self, stage: str):
        stage_options = {}
        if self.dist:
            stage_options.update(get_stage_options(stage, self.dist.kwargs))
        if self.component:
            stage_options.update(
                get_stage_options(stage, self.component.kwargs)
            )
        return stage_options

    def has_component_packages(self, stage: str):
        return (
            self.component is not None
            and self.component.has_packages
            and bool(self.get_parameters(stage).get("build", []))
        )

    def check_dependencies(self):
        def _check_component_sources(component_name, component_obj):
            if not (self.config.sources_dir / component_name).exists():
                raise PluginError(
                    f"Cannot find source component '{component_name}' in artifacts. "
                    f"Is package fetch stage done for '{component_name}'?"
                )
            self.log.info(
                "dependency '%s' (commit hash: %s)",
                component_name,
                component_obj.get_source_commit_hash(),
            )

        for dependency in self.dependencies:
            if isinstance(dependency, PluginDependency):
                if not self.manager.entities.get(dependency.reference, None):
                    raise PluginError(
                        f"Cannot find plugin '{dependency.reference}'."
                    )

            elif isinstance(dependency, ComponentDependency):
                component_name = dependency.reference
                components = self.config.get_components(
                    filtered_components=[component_name]
                )
                if not components:
                    raise PluginError(
                        f"Cannot find component '{component_name}' in configuration file."
                    )
                component_obj = components[0]
                _check_component_sources(component_name, component_obj)

            elif isinstance(dependency, JobDependency):
                ref = dependency.reference

                if ref.component is not None:
                    component_name = ref.component.name
                    _check_component_sources(component_name, ref.component)

                if not ref.build:
                    continue

                artifact_path = get_artifacts_path(self.config, ref)
                if not artifact_path or not artifact_path.exists():
                    raise PluginError(
                        f"Failed to retrieve artifact path for job '{str(ref)}'"
                    )

    def run(self, **kwargs):
        log_file = self.log.get_log_file()
        if log_file:
            self.log.info(f"Log file: {log_file}")
        self.check_dependencies()

    def get_cache_dir(self) -> Path:
        return (self.config.artifacts_dir / "cache").resolve()

    def get_sources_dir(self) -> Path:
        return (self.config.artifacts_dir / "sources").resolve()

    def get_repository_dir(self) -> Path:
        return (self.config.artifacts_dir / "repository").resolve()

    def get_repository_publish_dir(self) -> Path:
        return (self.config.artifacts_dir / "repository-publish").resolve()

    def get_distfiles_dir(self) -> Path:
        return (self.config.artifacts_dir / "distfiles").resolve()

    def get_templates_dir(self) -> Path:
        return (self.config.artifacts_dir / "templates").resolve()

    def get_installer_dir(self) -> Path:
        return (self.config.artifacts_dir / "installer").resolve()

    def get_iso_dir(self) -> Path:
        return (self.config.artifacts_dir / "iso").resolve()

    def get_component_distfiles_dir(self) -> Path:
        if self.component is None:
            raise PluginError(f"{self.name}: component is not set")
        return (self.config.distfiles_dir / self.component.name).resolve()

    def get_component_artifacts_dir(self, stage: str) -> Path:
        if self.component is None:
            raise PluginError(f"{self.name}: component is not set")
        path = (
            self.config.artifacts_dir
            / "components"
            / self.component.name
            / self.component.get_version_release()
            / "nodist"
            / stage
        )
        return path.resolve()

    def get_dist_component_artifacts_dir(self, stage: str) -> Path:
        if self.component is None:
            raise PluginError(f"{self.name}: component is not set")
        if self.dist is None:
            raise PluginError(f"{self.name}: dist is not set")
        path = (
            self.config.artifacts_dir
            / "components"
            / self.component.name
            / self.component.get_version_release()
            / self.dist.distribution
            / stage
        )
        return path.resolve()

    def get_dist_component_artifacts_dir_history(
        self, stage: str
    ) -> List[Path]:
        if self.component is None:
            raise PluginError(f"{self.name}: component is not set")
        if self.dist is None:
            raise PluginError(f"{self.name}: dist is not set")
        path = (
            self.config.artifacts_dir / "components" / self.component.name
        ).resolve()
        return list(path.glob(f"*/{self.dist.distribution}/{stage}"))

    @staticmethod
    def _get_artifacts_info(artifacts_path: Path):
        if not artifacts_path.exists():
            return {}
        try:
            with open(artifacts_path, "r") as f:
                artifacts_info = yaml.safe_load(f.read())
            return artifacts_info or {}
        except (PermissionError, yaml.YAMLError) as e:
            msg = f"Failed to read info from '{artifacts_path}'."
            raise PluginError(msg) from e

    def save_artifacts_info(
        self,
        stage: str,
        basename: str,
        info: dict,
        artifacts_dir: Path,
    ):
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(
                artifacts_dir
                / self.get_artifacts_info_filename(stage, basename),
                "w",
            ) as f:
                f.write(yaml.safe_dump(info))
        except (PermissionError, yaml.YAMLError) as e:
            msg = f"{basename}: Failed to write info for {stage} stage."
            raise PluginError(msg) from e

    def get_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Optional[Path] = None
    ) -> Dict:
        if artifacts_dir is None and self.component and not self.dist:
            artifacts_dir = self.get_component_artifacts_dir(stage)
        return self._get_artifacts_info(
            artifacts_dir / self.get_artifacts_info_filename(stage, basename)
        )

    def get_dist_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Optional[Path] = None
    ) -> Dict:
        return self.get_artifacts_info(
            stage,
            basename,
            artifacts_dir or self.get_dist_component_artifacts_dir(stage),
        )

    def save_dist_artifacts_info(
        self,
        stage: str,
        basename: str,
        info: dict,
        artifacts_dir: Optional[Path] = None,
    ):
        return self.save_artifacts_info(
            stage,
            basename,
            info,
            artifacts_dir or self.get_dist_component_artifacts_dir(stage),
        )

    def delete_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Optional[Path] = None
    ):
        if artifacts_dir is None and self.component and not self.dist:
            artifacts_dir = self.get_component_artifacts_dir(stage)
        info_path = artifacts_dir / self.get_artifacts_info_filename(
            stage, basename
        )
        if info_path.exists():
            info_path.unlink()

    def delete_dist_artifacts_info(
        self, stage: str, basename: str, artifacts_dir: Optional[Path] = None
    ):
        return self.delete_artifacts_info(
            stage,
            basename,
            artifacts_dir or self.get_dist_component_artifacts_dir(stage),
        )

    def check_stage_artifacts(
        self, stage: str, artifacts_dir: Optional[Path] = None
    ):
        for build in self.get_parameters(stage).get("build", []):
            build_bn = build.mangle()
            if not self.get_artifacts_info(
                stage=stage, basename=build_bn, artifacts_dir=artifacts_dir
            ):
                msg = f"Missing '{stage}' stage artifacts for {build_bn}!"
                raise PluginError(msg)

    def check_dist_stage_artifacts(
        self, stage: str, artifacts_dir: Optional[Path] = None
    ):
        return self.check_stage_artifacts(
            stage, artifacts_dir or self.get_dist_component_artifacts_dir(stage)
        )

    def get_template_artifacts_info(self, stage: str) -> Dict:
        if self.template is None:
            raise PluginError(f"{self.name}: template is not set")
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

    def delete_template_artifacts_info(self, stage: str):
        artifacts_dir = self.config.templates_dir
        info_path = artifacts_dir / f"{self.template}.{stage}.yml"
        if info_path.exists():
            info_path.unlink()

    def get_template_timestamp_for_stage(self, stage: str) -> Optional[str]:
        info = self.get_template_artifacts_info(stage)
        if not info:
            return None
        raw_ts = info.get("timestamp")
        if not raw_ts:
            return None
        try:
            return parsedate(raw_ts).strftime("%Y%m%d%H%M")
        except (dateutil.parser.ParserError, IndexError) as e:
            msg = f"{self.template}: Failed to parse {stage} timestamp format."
            raise PluginError(msg) from e

    def get_template_timestamp(self, stage: str = "build") -> str:
        if self.template is None:
            raise PluginError(f"{self.name}: template is not set")
        if not self.template.timestamp:
            ts = self.get_template_timestamp_for_stage(stage)
            if ts is None:
                raise PluginError(
                    f"{self.template}: Cannot determine template timestamp. Missing '{stage}' stage?"
                )
            self.template.timestamp = ts
        return self.template.timestamp

    def default_copy_in(self, plugins_dir: Path, sources_dir: Path):
        copy_in = [(self.manager.entities[self.name].directory, plugins_dir)]
        for dependency in self.dependencies:
            if isinstance(dependency, PluginDependency):
                copy_in.append(
                    (
                        self.manager.entities[dependency.reference].directory,
                        plugins_dir,
                    )
                )
            elif isinstance(dependency, ComponentDependency):
                copy_in.append(
                    (
                        self.config.sources_dir / dependency.reference,
                        sources_dir,
                    )
                )
            elif isinstance(dependency, JobDependency):
                job_ref = dependency.reference

                if job_ref.component is not None:
                    copy_in.append(
                        (
                            self.config.sources_dir / job_ref.component.name,
                            sources_dir,
                        )
                    )

                if job_ref.build is None:
                    continue

                artifact_path = get_artifacts_path(self.config, job_ref)
                info = self._get_artifacts_info(artifact_path)

                for file in info.get("files", []):
                    dependencies_dir = (
                        self.executor.get_dependencies_dir()
                        / get_relative_artifacts_path(job_ref).parent
                    )
                    copy_in.append(
                        (artifact_path.parent / file, dependencies_dir)
                    )

        return copy_in


# Compatibility classes for external plugins.
# External plugins that inherit from these classes continue to work without changes.


class ComponentPlugin(Plugin):
    context = PluginContext.COMPONENT

    @classmethod
    def from_args(cls, **kwargs) -> Optional["Plugin"]:
        if kwargs.get("stage") in cls.stages and kwargs.get("component"):
            return cls(
                component=kwargs["component"],
                config=kwargs["config"],
                stage=kwargs["stage"],
            )
        return None


class DistributionPlugin(Plugin):
    context = PluginContext.DIST

    @classmethod
    def supported_distribution(cls, distribution):
        raise NotImplementedError

    @classmethod
    def matches(cls, **kwargs) -> bool:
        if not super().matches(**kwargs):
            return False
        dist = kwargs.get("dist")
        try:
            return cls.supported_distribution(dist)
        except NotImplementedError:
            return True

    @classmethod
    def from_args(cls, **kwargs) -> Optional["Plugin"]:
        if kwargs.get("stage") in cls.stages and cls.supported_distribution(
            kwargs.get("dist")
        ):
            return cls(
                dist=kwargs["dist"],
                config=kwargs["config"],
                stage=kwargs["stage"],
            )
        return None


class DistributionComponentPlugin(Plugin):
    context = PluginContext.COMPONENT | PluginContext.DIST

    @classmethod
    def supported_distribution(cls, distribution):
        raise NotImplementedError

    @classmethod
    def matches(cls, **kwargs) -> bool:
        if not super().matches(**kwargs):
            return False
        dist = kwargs.get("dist")
        try:
            return cls.supported_distribution(dist)
        except NotImplementedError:
            return True

    @classmethod
    def from_args(cls, **kwargs) -> Optional["Plugin"]:
        if (
            kwargs.get("stage") in cls.stages
            and kwargs.get("component")
            and cls.supported_distribution(kwargs.get("dist"))
        ):
            return cls(
                component=kwargs["component"],
                dist=kwargs["dist"],
                config=kwargs["config"],
                stage=kwargs["stage"],
            )
        return None

    def run(self, **kwargs):
        super().run()
        if not self.get_parameters(self.stage).get("build", []):
            self.log.info(f"{self.component}:{self.dist}: Nothing to be done.")
            return


class TemplatePlugin(Plugin):
    context = PluginContext.TEMPLATE
    template: QubesTemplate

    @classmethod
    def supported_template(cls, template: QubesTemplate):
        raise NotImplementedError

    @classmethod
    def from_args(cls, **kwargs) -> Optional["Plugin"]:
        template = kwargs.get("template")
        if isinstance(template, QubesTemplate) and cls.supported_template(
            template
        ):
            return cls(
                template=template,
                config=kwargs["config"],
                stage=kwargs["stage"],
            )
        return None

    @classmethod
    def matches(cls, **kwargs) -> bool:
        if not super().matches(**kwargs):
            return False
        template = kwargs.get("template")
        if not isinstance(template, QubesTemplate):
            return False
        try:
            return cls.supported_template(template)
        except NotImplementedError:
            return True


class RPMDistributionPlugin(DistributionPlugin):
    dist_filter = staticmethod(lambda d: d.is_rpm())

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_rpm()


class DEBDistributionPlugin(DistributionPlugin):
    dist_filter = staticmethod(lambda d: d.is_deb() or d.is_ubuntu())

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_deb() or distribution.is_ubuntu()


class ArchlinuxDistributionPlugin(DistributionPlugin):
    dist_filter = staticmethod(lambda d: d.is_archlinux())

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_archlinux()


class GentooDistributionPlugin(DistributionPlugin):
    dist_filter = staticmethod(lambda d: d.is_gentoo())

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_gentoo()


class WindowsDistributionPlugin(DistributionPlugin):
    dist_filter = staticmethod(lambda d: d.is_windows())

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_windows()
