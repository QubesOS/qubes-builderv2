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
import re
from copy import deepcopy
from pathlib import Path
from typing import Union, List, Dict, Any

import yaml

from qubesbuilder.common import PROJECT_PATH, VerificationMode
from qubesbuilder.component import QubesComponent
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.exc import ConfigError
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.executors.qubes import LinuxQubesExecutor
from qubesbuilder.executors.windows import WindowsExecutor
from qubesbuilder.plugins import (
    DistributionPlugin,
    DistributionComponentPlugin,
    ComponentPlugin,
    TemplatePlugin,
)
from qubesbuilder.template import QubesTemplate

QUBES_RELEASE_RE = re.compile(r"r([1-9]\.[0-9]+).*")
QUBES_RELEASE_DEFAULT = "r4.2"


def extract_key_from_list(input_list: list):
    result = []
    for item in input_list:
        if isinstance(item, dict):
            if len(item.keys()) != 1:
                raise ConfigError(
                    f"More than one key defined dict in provided list: {input_list}."
                )
            result.append(next(iter(item.keys())))
        elif isinstance(item, str):
            result.append(item)
        else:
            raise ConfigError(f"Nested arrays are unsupported: {input_list}.")
    return result


def deep_merge(a: dict, b: dict, allow_append: bool = False) -> dict:
    result = deepcopy(a)
    for b_key, b_value in b.items():
        a_value = result.get(b_key, None)
        if isinstance(a_value, dict) and isinstance(b_value, dict):
            result[b_key] = deep_merge(a_value, b_value, allow_append)
        else:
            if isinstance(result.get(b_key, None), list) and allow_append:
                result[b_key] += deepcopy(b_value)
            else:
                result[b_key] = deepcopy(b_value)
    return result


class Config:
    def __init__(self, conf_file: Union[Path, str], options: dict = None):  # type: ignore
        # Keep path of configuration file
        self._conf_file = conf_file

        # Parse builder configuration file
        self._conf = self.parse_configuration_file(conf_file, options)

        # Qubes OS distributions
        self._dists: List = []

        # Default Qubes OS build pipeline stages
        self._stages: Dict = {}

        # Qubes OS components
        self._components: List[QubesComponent] = []

        # Qubes OS Templates
        self._templates: List[QubesTemplate] = []

        # Artifacts directory location
        self._artifacts_dir: Path = None  # type: ignore

        # Plugins directories
        self._plugins_dirs: List[Path] = [
            PROJECT_PATH / "qubesbuilder" / "plugins"
        ]

    # fmt: off
    # Mypy does not support this form yet (see https://github.com/python/mypy/issues/8083).
    verbose: Union[bool, property]                       = property(lambda self: self.get("verbose", False))
    debug: Union[bool, property]                         = property(lambda self: self.get("debug", False))
    force_fetch: Union[bool, property]                   = property(lambda self: self.get("force-fetch", False))
    skip_git_fetch: Union[bool, property]                = property(lambda self: self.get("skip-git-fetch", False))
    fetch_versions_only: Union[bool, property]           = property(lambda self: self.get("fetch-versions-only", False))
    backend_vmm: Union[str, property]                    = property(lambda self: self.get("backend-vmm", ""))
    use_qubes_repo: Union[Dict, property]                = property(lambda self: self.get("use-qubes-repo", {}))
    gpg_client: Union[str, property]                     = property(lambda self: self.get("gpg-client", "gpg"))
    sign_key: Union[Dict, property]                      = property(lambda self: self.get("sign-key", {}))
    min_age_days: Union[int, property]                   = property(lambda self: self.get("min-age-days", 5))
    qubes_release: Union[str, property]                  = property(lambda self: self.get("qubes-release", QUBES_RELEASE_DEFAULT))
    repository_publish: Union[Dict, property]            = property(lambda self: self.get("repository-publish", {}))
    repository_upload_remote_host: Union[Dict, property] = property(lambda self: self.get("repository-upload-remote-host", {}))
    template_root_size: Union[str, property]             = property(lambda self: self.get("template-root-size", "20G"))
    template_root_with_partitions: Union[bool, property] = property(lambda self: self.get("template-root-with-partitions", True))
    installer_kickstart: Union[str, property]            = property(lambda self: self.get("iso", {}).get("kickstart", "conf/qubes-kickstart.cfg"))
    installer_comps: Union[str, property]                = property(lambda self: self.get("iso", {}).get("comps", "comps/comps-dom0.xml"))
    iso_version: Union[str, property]                    = property(lambda self: self.get("iso", {}).get("version", ""))
    iso_flavor: Union[str, property]                     = property(lambda self: self.get("iso", {}).get("flavor", ""))
    iso_use_kernel_latest: Union[bool, property]         = property(lambda self: self.get("iso", {}).get("use-kernel-latest", False))
    iso_is_final: Union[bool, property]                  = property(lambda self: self.get("iso", {}).get("is-final", False))
    increment_devel_versions: Union[bool, property]      = property(lambda self: self.get("increment-devel-versions", False))
    automatic_upload_on_publish: Union[bool, property]   = property(lambda self: self.get("automatic-upload-on-publish", False))
    # fmt: on

    def __repr__(self):
        return f"<Config {str(self._conf_file)}>"

    @classmethod
    def _load_config(cls, conf_file: Path, options: dict = None):
        if not conf_file.exists():
            raise ConfigError(
                f"Cannot find builder configuration '{conf_file}'."
            )
        try:
            conf = yaml.safe_load(conf_file.read_text())
        except yaml.YAMLError as e:
            raise ConfigError(f"Failed to parse config '{conf_file}'.") from e

        included_conf = conf.get("include", [])
        conf.pop("include", None)

        included_data = []
        for inc in included_conf:
            inc_path = Path(inc)
            if not inc_path.is_absolute():
                inc_path = conf_file.parent / inc_path
            included_data.append(cls._load_config(inc_path))
        if options and isinstance(options, dict):
            included_data.append(options)

        # Override included values from main config as latest included data
        included_data.append(conf)

        # Init the final config based on included configs first
        combined_conf: Dict[str, Any] = {}
        for data in included_data:
            for key in data:
                if key in (
                    "+distributions",
                    "+templates",
                    "+components",
                    "+stages",
                    "+plugins",
                ):
                    combined_conf.setdefault(key, [])
                    combined_conf[key] += data[key]
                else:
                    # if conf top-level key is not defined or is a list we override by
                    # the included values, else we merge the two dicts where included
                    # values may override original ones.
                    if combined_conf.get(key, None) and isinstance(
                        combined_conf[key], dict
                    ):
                        combined_conf[key] = deep_merge(
                            combined_conf[key], data[key]
                        )
                    else:
                        combined_conf[key] = data[key]

        # Allow options to override only values that can be merged
        if options and isinstance(options, dict):
            for key in options:
                if not key.startswith("+") and key not in (
                    "distributions",
                    "templates",
                    "components",
                    "stages",
                    "plugins",
                ):
                    if isinstance(combined_conf[key], dict) and isinstance(
                        options[key], dict
                    ):
                        combined_conf[key] = deep_merge(
                            combined_conf[key], options[key]
                        )
                    else:
                        combined_conf[key] = options[key]

        return combined_conf

    @classmethod
    def parse_configuration_file(
        cls, conf_file: Union[Path, str], options: Dict = None
    ):
        if isinstance(conf_file, str):
            conf_file = Path(conf_file).resolve()

        final_conf = cls._load_config(conf_file, options)

        # Merge dict from included configs
        for key in (
            "distributions",
            "templates",
            "components",
            "stages",
            "plugins",
            "git",
        ):
            if f"+{key}" in final_conf.keys():
                merged_result: Dict[str, Dict] = {}
                final_conf.setdefault(key, [])
                final_conf.setdefault(f"+{key}", [])
                # Iterate over all key and +key in order to merge dicts
                # FIXME: we should improve here how we merge
                for s in final_conf[key] + final_conf[f"+{key}"]:
                    if isinstance(s, str) and not merged_result.get(s, None):
                        merged_result[s] = {}
                    if isinstance(s, dict):
                        if not merged_result.get(next(iter(s.keys())), None):
                            merged_result[next(iter(s.keys()))] = next(
                                iter(s.values())
                            )
                        else:
                            merged_result[next(iter(s.keys()))] = deep_merge(
                                merged_result[next(iter(s.keys()))],
                                next(iter(s.values())),
                            )
                # Set final value based on merged dict
                final_conf[key] = []
                for k, v in merged_result.items():
                    if not v:
                        final_conf[key].append(k)
                    else:
                        final_conf[key].append({k: v})
        return final_conf

    def get(self, key, default=None):
        return self._conf.get(key, default)

    def set(self, key, value):
        self._conf[key] = value

    def get_conf_path(self) -> Path:
        conf_file = self._conf_file
        if isinstance(conf_file, str):
            conf_file = Path(conf_file).expanduser().resolve()
        return conf_file

    def get_distributions(self, filtered_distributions=None):
        if not self._dists:
            distributions = self._conf.get("distributions", [])
            for dist in distributions:
                dist_name = dist
                dist_options = {}
                if isinstance(dist, dict):
                    dist_name = next(iter(dist.keys()))
                    dist_options = next(iter(dist.values()))
                self._dists.append(QubesDistribution(dist_name, **dist_options))
        if filtered_distributions:
            result = []
            for fd in filtered_distributions:
                for d in self._dists:
                    if d.distribution == fd:
                        result.append(d)
                        break
                else:
                    raise ConfigError(f"No such distribution: {fd}")
            return result
        return self._dists

    def get_templates(self, filtered_templates=None):
        if not self._templates:
            templates = self._conf.get("templates", [])
            self._templates = [
                QubesTemplate(template) for template in templates
            ]
        if filtered_templates:
            result = []
            for ft in filtered_templates:
                for t in self._templates:
                    if t.name == ft:
                        result.append(t)
                        break
                else:
                    raise ConfigError(f"No such template: {ft}")
            return result
        return self._templates

    def get_components(self, filtered_components=None, url_match=False):
        if not self._components:
            # Load available component information from config
            components_from_config = []
            for c in self._conf.get("components", []):
                components_from_config.append(
                    self.get_component_from_dict_or_string(c)
                )

            self._components = components_from_config

        # Find if components requested would have been found from config file with
        # non default values for url, maintainer, etc.
        if filtered_components:
            result = []
            prefix = self.get("git", {}).get("prefix", "QubesOS/qubes-")
            for fc in filtered_components:
                found = False
                for c in self._components:
                    if c.name == fc:
                        result.append(c)
                        found = True
                    elif url_match and c.url.partition(prefix)[2] == fc:
                        result.append(c)
                        found = True
                if not found:
                    raise ConfigError(f"No such component: {fc}")
            return result
        else:
            return self._components

    @property
    def artifacts_dir(self):
        if not self._artifacts_dir:
            if self._conf.get("artifacts-dir", None):
                self._artifacts_dir = Path(
                    self._conf["artifacts-dir"]
                ).resolve()
            else:
                self._artifacts_dir = PROJECT_PATH / "artifacts"
        return self._artifacts_dir

    @property
    def temp_dir(self):
        return self.artifacts_dir / "tmp"

    @property
    def cache_dir(self):
        return self.artifacts_dir / "cache"

    @property
    def sources_dir(self):
        return self.artifacts_dir / "sources"

    @property
    def repository_dir(self):
        return self.artifacts_dir / "repository"

    @property
    def repository_publish_dir(self):
        return self.artifacts_dir / "repository-publish"

    @property
    def distfiles_dir(self):
        return self.artifacts_dir / "distfiles"

    @property
    def templates_dir(self):
        return self.artifacts_dir / "templates"

    @property
    def installer_dir(self):
        return self.artifacts_dir / "installer"

    @property
    def iso_dir(self):
        return self.artifacts_dir / "iso"

    @property
    def logs_dir(self):
        return self.artifacts_dir / "logs"

    def get_plugins_dirs(self):
        plugins_dirs = self._conf.get("plugins-dirs", [])
        # We call get_components in order to ensure that plugin ones are added
        # in _plugins_dirs.
        self.get_components()
        for d in self._plugins_dirs:
            d_path = Path(d).expanduser().resolve()
            if d not in plugins_dirs:
                plugins_dirs = plugins_dirs + [str(d_path)]
        return plugins_dirs

    def get_executor_options_from_config(
        self,
        stage_name: str,
        plugin: Union[
            DistributionPlugin,
            DistributionComponentPlugin,
            ComponentPlugin,
            TemplatePlugin,
        ] = None,
    ):
        dist = None
        component = None
        distribution_executor_options = {}
        component_executor_options: Dict[Any, Any] = {}
        default_executor_options = self._conf.get("executor", {}) or {}
        stage_executor_options = {}
        executor_options: Dict[Any, Any] = {}

        if plugin:
            if hasattr(plugin, "component"):
                component = plugin.component
            if hasattr(plugin, "dist"):
                dist = plugin.dist

        if dist and isinstance(dist, QubesDistribution):
            for distribution in self.get_distributions():
                if dist == distribution:
                    for stage in distribution.kwargs.get("stages", []):
                        if (
                            isinstance(stage, dict)
                            and next(iter(stage)) == stage_name
                            and isinstance(stage[stage_name], dict)
                        ):
                            distribution_executor_options = stage[
                                stage_name
                            ].get("executor", {})
                            break

        if component and isinstance(component, QubesComponent):
            for comp in self.get_components():
                if comp == component:
                    distribution_stages = []
                    package_set_stages = []
                    if dist and dist.distribution in comp.kwargs:
                        distribution_stages = comp.kwargs[
                            dist.distribution
                        ].get("stages", [])
                    if dist and dist.package_set in comp.kwargs:
                        package_set_stages = comp.kwargs.get(
                            dist.package_set, {}
                        ).get("stages", [])
                    component_stages = comp.kwargs.get("stages", [])

                    for stage in (
                        component_stages
                        + package_set_stages
                        + distribution_stages
                    ):
                        if (
                            isinstance(stage, dict)
                            and next(iter(stage)) == stage_name
                            and isinstance(stage[stage_name], dict)
                        ):
                            component_executor_options = deep_merge(
                                component_executor_options,
                                stage[stage_name].get("executor", {}),
                            )

        for stage in self._conf.get("stages", []):
            if isinstance(stage, str):
                continue
            if (
                isinstance(stage, dict)
                and next(iter(stage)) == stage_name
                and isinstance(stage[stage_name], dict)
            ):
                stage_executor_options = stage[stage_name].get("executor", {})
                break

        for options in [
            default_executor_options,
            stage_executor_options,
            component_executor_options,
            distribution_executor_options,
        ]:
            executor_options = deep_merge(executor_options, options)

        return executor_options

    def get_executor_from_config(
        self,
        stage_name: str,
        plugin: Union[
            DistributionPlugin,
            DistributionComponentPlugin,
            ComponentPlugin,
            TemplatePlugin,
        ] = None,
    ):
        executor_options = self.get_executor_options_from_config(
            stage_name, plugin
        )
        executor = self.get_executor(executor_options)
        if not executor:
            raise ConfigError(
                "No defined executor found in configuration file."
            )
        if plugin:
            executor.log = plugin.log.getChild(stage_name)
        return executor

    def get_component_from_dict_or_string(
        self, component_name: Union[str, Dict]
    ) -> QubesComponent:
        baseurl = self.get("git", {}).get("baseurl", "https://github.com")
        prefix = self.get("git", {}).get("prefix", "QubesOS/qubes-")
        suffix = self.get("git", {}).get("suffix", ".git")
        branch = self.get("git", {}).get("branch", "main")
        maintainers = self.get("git", {}).get("maintainers", [])
        timeout = self.get("timeout", 3600)
        min_distinct_maintainers = self.get("min-distinct-maintainers", 1)

        if isinstance(component_name, str):
            component_name = {component_name: {}}

        name, options = next(iter(component_name.items()))
        source_dir = self.artifacts_dir / "sources" / name
        url = f"{baseurl}/{options.get('prefix', prefix)}{name}{options.get('suffix', suffix)}"
        verification_mode = VerificationMode.SignedTag
        if name in self._conf.get("insecure-skip-checking", []):
            verification_mode = VerificationMode.Insecure
        if name in self._conf.get("less-secure-signed-commits-sufficient", []):
            verification_mode = VerificationMode.SignedCommit
        if "verification-mode" in options:
            verification_mode = VerificationMode(options["verification-mode"])
        fetch_versions_only = options.get(
            "fetch-versions-only", self.get("fetch-versions-only", False)
        )
        is_plugin = options.get("plugin", False)
        has_packages = options.get("packages", True)

        component_kwargs = {
            "source_dir": source_dir,
            "url": options.get("url", url),
            "branch": options.get("branch", branch),
            "maintainers": options.get("maintainers", maintainers),
            "verification_mode": verification_mode,
            "timeout": options.get("timeout", timeout),
            "fetch_versions_only": fetch_versions_only,
            "is_plugin": is_plugin,
            "has_packages": has_packages,
            "min_distinct_maintainers": options.get(
                "min-distinct-maintainers", min_distinct_maintainers
            ),
            **options,
        }
        if self.increment_devel_versions:
            component_kwargs["devel_path"] = (
                self.artifacts_dir / "components" / name / "noversion" / "devel"
            )
        if is_plugin:
            plugin_dir = source_dir
            if options.get("content-dir", None):
                plugin_dir = source_dir / options["content-dir"]
            self._plugins_dirs.append(plugin_dir)
        return QubesComponent(**component_kwargs)

    @staticmethod
    def get_executor(options):
        executor_type = options.get("type")
        executor_options = {}
        for key, val in options.get("options", {}).items():
            new_key = key.replace("-", "_") if "-" in key else key
            executor_options[new_key] = val
        if executor_type in ("podman", "docker"):
            executor = ContainerExecutor(executor_type, **executor_options)
        elif executor_type == "local":
            executor = LocalExecutor(**executor_options)  # type: ignore
        elif executor_type == "qubes":
            executor = LinuxQubesExecutor(**executor_options)  # type: ignore
        elif executor_type == "windows":
            executor = WindowsExecutor(**executor_options)  # type: ignore
        else:
            raise ExecutorError("Cannot determine which executor to use.")
        return executor

    def get_absolute_path_from_config(self, config_path_str, relative_to=None):
        if config_path_str.startswith("./"):
            config_path = self.get_conf_path().parent / config_path_str
        elif config_path_str.startswith("~"):
            config_path = Path(config_path_str).expanduser()
        elif not config_path_str.startswith("/"):
            if not relative_to:
                raise ConfigError(
                    "Cannot determine path: please provide relative path."
                )
            config_path = relative_to / config_path_str
        else:
            config_path = Path(config_path_str).resolve()
        return config_path

    def parse_qubes_release(self):
        parsed_release = QUBES_RELEASE_RE.match(
            self.qubes_release
        ) or QUBES_RELEASE_RE.match(QUBES_RELEASE_DEFAULT)
        if not parsed_release:
            raise ConfigError(
                f"Cannot parse Qubes OS release: '{self.qubes_release}'"
            )
        return parsed_release
