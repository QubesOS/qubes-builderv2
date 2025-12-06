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
import datetime
import itertools
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List

import dateutil.parser
import yaml
from dateutil.parser import parse as parsedate

from qubesbuilder.config import Config, ConfigError
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.plugins import (
    PluginError,
    DistributionPlugin,
    PluginDependency,
    ComponentDependency,
)
from qubesbuilder.template import QubesTemplate


class InstallerError(PluginError):
    pass


class InstallerPlugin(DistributionPlugin):
    """
    InstallerPlugin creates Qubes OS ISO
    """

    name = "installer"
    stages = ["init-cache", "prep", "build", "sign", "upload"]

    def __init__(
        self,
        dist: QubesDistribution,
        config: Config,
        stage: str,
        templates: List[QubesTemplate] = None,
        **kwargs,
    ):
        super().__init__(config=config, dist=dist, stage=stage, **kwargs)

        self.dependencies += [
            PluginDependency("chroot_rpm"),
            ComponentDependency("qubes-release"),
        ]

        self.iso_name = ""
        self.iso_version = ""
        self.iso_timestamp = ""
        self.templates = templates or []

        self.kickstart_path = self.config.get_absolute_path_from_config(
            config.installer_kickstart,
            relative_to=self.config.sources_dir / "qubes-release",
        )
        self.comps_path = self.config.get_absolute_path_from_config(
            config.installer_comps,
            relative_to=self.config.sources_dir / "qubes-release",
        )

        if not self.kickstart_path.exists():
            raise InstallerError(
                f"Cannot find kickstart: '{self.kickstart_path}'"
            )

        if not self.comps_path.exists():
            raise InstallerError(f"Cannot find comps: '{self.comps_path}'")

    def get_iso_timestamp(self, stage: str, iso_timestamp: str = None) -> str:
        if not self.iso_timestamp:
            # Determine latest timestamp filename
            installer_dir = self.config.installer_dir
            if self.config.iso_flavor:
                iso_timestamp_file = (
                    installer_dir
                    / f"latest_{self.dist.name}_iso_{self.config.iso_flavor}_timestamp"
                )
            else:
                iso_timestamp_file = (
                    installer_dir / f"latest_{self.dist.name}_iso_timestamp"
                )

            # Create timestamp value for "prep" only
            if stage == "prep":
                if iso_timestamp:
                    self.iso_timestamp = parsedate(iso_timestamp).strftime(
                        "%Y%m%d%H%M"
                    )
                else:
                    self.iso_timestamp = datetime.datetime.now(
                        datetime.UTC
                    ).strftime("%Y%m%d%H%M")
                installer_dir.mkdir(parents=True, exist_ok=True)
                with open(iso_timestamp_file, "w") as f:
                    f.write(self.iso_timestamp)
            else:
                # Read information from build stage
                if not iso_timestamp_file.exists():
                    raise PluginError(
                        f"{self.dist}: Cannot find build timestamp."
                    )
                with open(iso_timestamp_file) as f:
                    data = f.read().splitlines()
                try:
                    self.iso_timestamp = parsedate(data[0]).strftime(
                        "%Y%m%d%H%M"
                    )
                except (dateutil.parser.ParserError, IndexError) as e:
                    msg = (
                        f"{self.dist}: Failed to parse build timestamp format."
                    )
                    raise PluginError(msg) from e
        return self.iso_timestamp

    def update_parameters(self, stage: str, iso_timestamp: str = None):
        self.environment.update(
            {
                "DIST": self.dist.name,
                "INSTALL_DIR": f"{self.executor.get_builder_dir()}/mnt",
                "ARTIFACTS_DIR": str(self.executor.get_build_dir()),
                "PLUGINS_DIR": str(self.executor.get_plugins_dir()),
                "PACKAGES_DIR": str(self.executor.get_repository_dir()),
                "CACHE_DIR": str(self.executor.get_cache_dir()),
                "ISO_USE_KERNEL_LATEST": (
                    "1" if self.config.iso_use_kernel_latest else "0"
                ),
                "ISO_IS_FINAL": "1" if self.config.iso_is_final else "0",
            }
        )
        if self.config.use_qubes_repo:
            self.environment.update(
                {
                    "USE_QUBES_REPO_VERSION": str(
                        self.config.use_qubes_repo.get("version", None)
                    ),
                    "USE_QUBES_REPO_TESTING": (
                        "1"
                        if self.config.use_qubes_repo.get("testing", None)
                        else "0"
                    ),
                }
            )

        # Kickstart will be copied under installer conf directory
        self.environment["INSTALLER_KICKSTART"] = (
            self.executor.get_sources_dir()
            / f"qubes-release/conf/_builder_{self.kickstart_path.name}"
        )

        # Comps will be copied under builder directory
        self.environment["COMPS_FILE"] = (
            self.executor.get_builder_dir() / self.comps_path.name
        )

        # We don't need to process more ISO information
        if stage == "init-cache":
            return

        self.iso_version = self.get_iso_timestamp(
            stage=stage, iso_timestamp=iso_timestamp
        )
        if self.config.iso_version:
            self.iso_version = self.config.iso_version

        if self.config.iso_flavor:
            self.environment["ISO_FLAVOR"] = self.config.iso_flavor
            self.iso_name = f"Qubes-{self.iso_version}-{self.config.iso_flavor}-{self.dist.architecture}"
        else:
            self.iso_name = f"Qubes-{self.iso_version}-{self.dist.architecture}"

        self.environment.update(
            {"ISO_VERSION": self.iso_version, "ISO_NAME": self.iso_name}
        )

    def get_installer_artifacts_info(self, stage: str) -> Dict:
        fileinfo = (
            self.config.installer_dir
            / f"{self.dist.name}_{self.iso_name}.{stage}.yml"
        )
        if fileinfo.exists():
            try:
                with open(fileinfo, "r") as f:
                    artifacts_info = yaml.safe_load(f.read())
                return artifacts_info or {}
            except (PermissionError, yaml.YAMLError) as e:
                msg = f"{self.dist}: Failed to read info from {stage} stage."
                raise PluginError(msg) from e
        return {}

    def delete_artifacts_info(self, stage: str):
        artifacts_dir = self.config.installer_dir
        info_path = (
            artifacts_dir / f"{self.dist.name}_{self.iso_name}.{stage}.yml"
        )
        if info_path.exists():
            info_path.unlink()

    def get_env(self):
        env = []
        for key, val in self.environment.items():
            env.append(f'{key}="{val}"')
        return " ".join(env)

    def templates_copy_in(self, executor):
        for template in self.templates:
            template_info = self.get_artifacts_info(
                "build", template.name, self.config.templates_dir
            )
            if not template_info or len(template_info.get("rpms", [])) != 1:
                self.log.warning(
                    f"{self.dist}: Template {template.name} is not built locally."
                )
                continue
            rpm_path = (
                self.config.templates_dir / "rpm" / template_info["rpms"][0]
            )
            if not rpm_path.exists():
                self.log.warning(
                    f"{self.dist}:{template.name}: Cannot find {rpm_path}."
                )
                continue
            yield rpm_path, self.executor.get_repository_dir()

    def run(
        self,
        iso_timestamp: str = None,
        cache_templates_only: bool = False,
        **kwargs,
    ):
        super().run()

        self.update_parameters(stage=self.stage, iso_timestamp=iso_timestamp)

        mock_conf = f"{self.dist.fullname}-{self.dist.version}-{self.dist.architecture}.cfg"
        repository_dir = self.config.repository_dir / self.dist.distribution

        cache_dir = self.config.cache_dir / "installer"
        chroot_cache = (
            self.config.cache_dir
            / "installer/chroot/mock"
            / mock_conf.replace(".cfg", "")
        )

        iso_dir = self.config.iso_dir
        iso_dir.mkdir(parents=True, exist_ok=True)
        iso = iso_dir / f"{self.iso_name}.iso"

        chroot_dir = cache_dir / "chroot/mock"
        templates_cache_dir = cache_dir / "templates"

        if iso.exists() and self.stage in ("prep", "build"):
            msg = f"{self.dist}:{self.iso_name}: ISO already exists."
            raise InstallerError(msg)

        if not iso.exists() and self.stage in ("sign", "upload"):
            msg = f"{self.iso_name}: Cannot find ISO '{iso}'."
            raise InstallerError(msg)

        # Determine template version based on Qubes OS release
        try:
            parsed_release = self.config.parse_qubes_release()
        except ConfigError as e:
            raise InstallerError(
                f"Cannot determine template version: {str(e)}"
            ) from e

        templates = [
            f"qubes-template-{t}-{parsed_release.group(1)}.0"
            for t in self.config.get("cache", {}).get("templates", [])
        ]

        if self.stage == "init-cache":
            chroot_dir.mkdir(exist_ok=True, parents=True)

            if not cache_templates_only:
                # FIXME: Parse from mock cfg?
                mock_chroot_name = mock_conf.replace(".cfg", "")
                if (chroot_dir / mock_chroot_name).exists():
                    shutil.rmtree(chroot_dir / mock_chroot_name)

                copy_in = self.default_copy_in(
                    self.executor.get_plugins_dir(),
                    self.executor.get_sources_dir(),
                )

                # Copy-in builder local repository
                if repository_dir.exists():
                    copy_in += [
                        (repository_dir, self.executor.get_repository_dir())
                    ]
                copy_in.extend(self.templates_copy_in(self.executor))

                copy_out = [
                    (
                        self.executor.get_cache_dir()
                        / f"mock/{mock_chroot_name}",
                        chroot_dir,
                    )
                ]

                mock_cmd = [
                    f"sudo --preserve-env=DIST,USE_QUBES_REPO_VERSION",
                    f"/usr/libexec/mock/mock",
                    f"--root {self.executor.get_plugins_dir()}/installer/mock/{mock_conf}",
                    "--init",
                ]
                if isinstance(self.executor, ContainerExecutor):
                    msg = (
                        f"{self.dist}: Mock isolation set to 'simple', build has full network "
                        f"access. Use 'qubes' executor for network-isolated build."
                    )
                    self.log.warning(msg)
                    mock_cmd.append("--isolation=simple")
                else:
                    mock_cmd.append("--isolation=nspawn")
                if self.config.verbose:
                    mock_cmd.append("--verbose")
                if (
                    self.config.use_qubes_repo
                    and self.config.use_qubes_repo.get("version")
                ):
                    mock_cmd.append("--enablerepo=qubes-current")
                if (
                    self.config.use_qubes_repo
                    and self.config.use_qubes_repo.get("testing")
                ):
                    mock_cmd.append("--enablerepo=qubes-current-testing")

                files_inside_executor_with_placeholders = [
                    f"@PLUGINS_DIR@/installer/mock/{mock_conf}"
                ]

                # Create builder-local repository (could be empty) inside the cage
                cmd_for_build_repo = [
                    f"mkdir -p {self.executor.get_repository_dir()}",
                    f"cd {self.executor.get_repository_dir()}",
                    "createrepo_c .",
                ]

                cmd = cmd_for_build_repo + [" ".join(mock_cmd)]
                cmd += [
                    f"sudo chmod a+rX -R {self.executor.get_cache_dir()}/mock/{mock_chroot_name}/dnf_cache/"
                ]

                try:
                    self.executor.run(
                        cmd,
                        copy_in,
                        copy_out,
                        environment=self.environment,
                        files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
                    )
                except ExecutorError as e:
                    msg = f"{self.dist}: Failed to generate chroot: {str(e)}."
                    raise InstallerError(msg) from e

            # Create a second cage for downloading the templates
            if templates:
                templates_cache_dir.mkdir(exist_ok=True, parents=True)
                self.environment["TEMPLATE_PACKAGES"] = " ".join(templates)

                # Temporary dir for downloaded templates that we
                # will merge into templates_cache_dir.
                temp_dir = Path(tempfile.mkdtemp(dir=self.config.temp_dir))

                copy_in = self.default_copy_in(
                    self.executor.get_plugins_dir(),
                    self.executor.get_sources_dir(),
                ) + [
                    (self.kickstart_path, self.executor.get_builder_dir()),
                    (self.comps_path, self.executor.get_builder_dir()),
                    (templates_cache_dir, self.executor.get_repository_dir()),
                ]
                copy_out = [
                    (
                        self.executor.get_repository_dir()
                        / templates_cache_dir.name,
                        temp_dir,
                    )
                ]
                cmd = [
                    f"mv {self.executor.get_builder_dir() / self.kickstart_path.name} {self.executor.get_sources_dir()}/qubes-release/conf/_builder_{self.kickstart_path.name}",
                    f"sudo --preserve-env={','.join(self.environment.keys())} make -C {self.executor.get_plugins_dir()}/installer iso-parse-kickstart iso-templates-cache",
                ]
                try:
                    self.executor.run(
                        cmd, copy_in, copy_out, environment=self.environment
                    )
                except ExecutorError as e:
                    msg = (
                        f"{self.dist}: Failed to download templates: {str(e)}."
                    )
                    raise InstallerError(msg) from e

                # Merge downloaded templates into the temporary
                # directory into templates cache dir
                local_executor = LocalExecutor()
                local_executor.log = self.log.getChild(self.stage)
                try:
                    copy_in = self.default_copy_in(
                        local_executor.get_plugins_dir(),
                        local_executor.get_sources_dir(),
                    )
                    merge_cmd = [
                        str(
                            local_executor.get_plugins_dir()
                            / "installer/scripts/update-templates-cache"
                        ),
                        str(temp_dir / "templates"),
                        str(templates_cache_dir),
                    ]
                    cmd = [" ".join(map(shlex.quote, merge_cmd))]
                    local_executor.run(
                        cmd, copy_in=copy_in, environment=self.environment
                    )
                except ExecutorError as e:
                    raise InstallerError(
                        f"Failed to update templates cache: {str(e)}."
                    )
                finally:
                    shutil.rmtree(temp_dir)

        if self.stage == "prep":
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (self.kickstart_path, self.executor.get_builder_dir()),
                (self.comps_path, self.executor.get_builder_dir()),
            ]

            # Copy-in builder local repository
            if repository_dir.exists():
                copy_in += [
                    (repository_dir, self.executor.get_repository_dir())
                ]
            copy_in.extend(self.templates_copy_in(self.executor))

            # Prepare cmd
            cmd = [
                f"mv {self.executor.get_builder_dir() / self.kickstart_path.name} {self.executor.get_sources_dir()}/qubes-release/conf/_builder_{self.kickstart_path.name}"
            ]

            # Create builder-local repository (could be empty) inside the cage
            cmd += [
                f"mkdir -p {self.executor.get_repository_dir()}",
                f"cd {self.executor.get_repository_dir()}",
                "createrepo_c .",
            ]

            # Add prepared chroot cache
            if chroot_cache.exists():
                copy_in += [
                    (chroot_cache.parent, self.executor.get_cache_dir())
                ]
                cmd += [
                    f"sudo chown -R root:mock {self.executor.get_cache_dir() / 'mock'}"
                ]

            # Add downloaded templates into builder-local repository
            if templates_cache_dir.exists():
                copy_in += [
                    (templates_cache_dir, self.executor.get_repository_dir())
                ]

            copy_out = [
                (
                    self.executor.get_plugins_dir() / f"installer/work",
                    cache_dir / self.iso_name,
                ),
                (
                    self.executor.get_plugins_dir()
                    / f"installer/yum/installer/rpm",
                    cache_dir / self.iso_name,
                ),
                (
                    self.executor.get_builder_dir()
                    / f"dnfroot/etc/yum.repos.d/installer.repo",
                    cache_dir / self.iso_name,
                ),
            ]

            #
            # Inside mock chroot to generate our packages list (qubes-pykickstart)
            #

            mock_cmd = [
                f"sudo --preserve-env={','.join(self.environment.keys())}",
                f"/usr/libexec/mock/mock",
                f"--root {self.executor.get_plugins_dir()}/installer/mock/{mock_conf}",
                f"--chroot 'env {self.get_env()} make -C {self.executor.get_plugins_dir()}/installer iso-prepare iso-parse-kickstart iso-parse-tmpl'",
            ]
            if isinstance(self.executor, ContainerExecutor):
                msg = (
                    f"{self.dist}: Mock isolation set to 'simple', build has full network "
                    f"access. Use 'qubes' executor for network-isolated build."
                )
                self.log.warning(msg)
                mock_cmd.append("--isolation=simple")
            else:
                mock_cmd.append("--isolation=nspawn")
            if self.config.verbose:
                mock_cmd.append("--verbose")
            if self.config.use_qubes_repo and self.config.use_qubes_repo.get(
                "version"
            ):
                mock_cmd.append("--enablerepo=qubes-current")
            if self.config.use_qubes_repo and self.config.use_qubes_repo.get(
                "testing"
            ):
                mock_cmd.append("--enablerepo=qubes-current-testing")
            if chroot_cache.exists():
                mock_cmd.append("--plugin-option=root_cache:age_check=False")

            # Disable builder-local repository inside the cage if it does not exist
            if not repository_dir.exists():
                mock_cmd.append("--disablerepo=builder-local")

            files_inside_executor_with_placeholders = [
                f"@PLUGINS_DIR@/installer/mock/{mock_conf}"
            ]

            cmd += [" ".join(mock_cmd)]

            #
            # Outside mock chroot to use the latest packages (dnf, openssl, etc.) from the cage.
            #

            cmd += [
                f"sudo --preserve-env={','.join(self.environment.keys())} "
                f"make -C {self.executor.get_plugins_dir()}/installer "
                f"iso-prepare iso-packages-anaconda iso-packages-lorax",
            ]

            try:
                self.executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                    files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
                )
            except ExecutorError as e:
                msg = f"{self.dist}: Failed to prepare installer: {str(e)}."
                raise InstallerError(msg) from e

            # Check we have only RPMs in installer (Lorax) and work (Anaconda) repositories
            for f in itertools.chain(
                (cache_dir / self.iso_name / "rpm").glob("*"),
                (
                    cache_dir
                    / f"{self.iso_name}/work/{self.iso_version}/{self.dist.architecture}/os/Packages"
                ).glob("*"),
            ):
                if f.suffix != ".rpm":
                    raise InstallerError(
                        f"File with forbidden extension detected: '{f}'."
                    )

        if self.stage == "build":
            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [
                (
                    cache_dir / self.iso_name / "work",
                    self.executor.get_plugins_dir() / "installer",
                ),
                (
                    cache_dir / self.iso_name / "rpm",
                    self.executor.get_plugins_dir() / "installer/yum/installer",
                ),
                (self.kickstart_path, self.executor.get_builder_dir()),
                (self.comps_path, self.executor.get_builder_dir()),
            ]

            # Copy-in builder local repository
            if repository_dir.exists():
                copy_in += [
                    (repository_dir, self.executor.get_repository_dir())
                ]
            copy_in.extend(self.templates_copy_in(self.executor))

            # Prepare installer cmd
            cmd = [
                f"mv {self.executor.get_builder_dir() / self.kickstart_path.name} {self.executor.get_sources_dir()}/qubes-release/conf/_builder_{self.kickstart_path.name}"
            ]

            # Add prepared chroot cache
            if chroot_cache.exists():
                copy_in += [
                    (chroot_cache.parent, self.executor.get_cache_dir())
                ]
                cmd += [
                    f"sudo chown -R root:mock {self.executor.get_cache_dir() / 'mock'}"
                ]

            copy_out = [
                (
                    self.executor.get_plugins_dir()
                    / f"installer/work/{self.iso_version}/{self.dist.architecture}/iso/{self.iso_name}.iso",
                    iso_dir,
                )
            ]

            # Create builder-local repository (could be empty) inside the cage
            cmd += [
                f"mkdir -p {self.executor.get_repository_dir()}",
                f"cd {self.executor.get_repository_dir()}",
                "createrepo_c .",
            ]

            #
            # Inside mock chroot to use qubes-lorax
            #

            mock_cmd = [
                f"sudo --preserve-env={','.join(self.environment.keys())}",
                f"/usr/libexec/mock/mock",
                f"--root {self.executor.get_plugins_dir()}/installer/mock/{mock_conf}",
                "--disablerepo='*'",
                f"--chroot 'env {self.get_env()} make -C {self.executor.get_plugins_dir()}/installer iso-prepare iso-parse-kickstart iso-installer-lorax iso-installer-mkisofs'",
            ]
            if isinstance(self.executor, ContainerExecutor):
                msg = (
                    f"{self.dist}: Mock isolation set to 'simple', build has full network "
                    f"access. Use 'qubes' executor for network-isolated build."
                )
                self.log.warning(msg)
                mock_cmd.append("--isolation=simple")
            else:
                mock_cmd.append("--isolation=nspawn")
            if self.config.verbose:
                mock_cmd.append("--verbose")
            if chroot_cache.exists():
                mock_cmd.append("--plugin-option=root_cache:age_check=False")

            files_inside_executor_with_placeholders = [
                f"@PLUGINS_DIR@/installer/mock/{mock_conf}"
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
                msg = f"{self.dist}: Failed to create ISO: {str(e)}."
                raise InstallerError(msg) from e

            # Save ISO information we built
            info = {
                "iso": iso.name,
                "version": self.iso_version,
                "timestamp": self.get_iso_timestamp(self.stage),
                "kickstart": str(self.config.installer_kickstart),
                "packages": {
                    "runtime": [
                        pkg.name
                        for pkg in (cache_dir / self.iso_name / "rpm").glob(
                            "*.rpm"
                        )
                    ],
                    "anaconda": [
                        pkg.name
                        for pkg in (
                            cache_dir
                            / f"{self.iso_name}/work/{self.iso_version}/{self.dist.architecture}/os/Packages"
                        ).glob("*.rpm")
                    ],
                },
            }
            self.save_artifacts_info(
                self.stage,
                f"{self.dist.name}_{self.iso_name}",
                info,
                self.config.installer_dir,
            )

        if self.stage == "sign":
            # Check if we have a signing key provided
            sign_key = self.config.sign_key.get("iso", None)
            if not sign_key:
                self.log.info(f"{self.dist}: No signing key found.")
                return

            # Check if we have a gpg client provided
            if not self.config.gpg_client:
                self.log.info(f"Please specify GPG client to use!")
                return

            try:
                self.log.info(f"{self.iso_name}: Signing '{iso.name}'.")
                cmd = [
                    f"{self.manager.entities['installer'].directory}/scripts/release-iso {iso} {self.config.gpg_client} {sign_key}"
                ]
                self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.iso_name}: Failed to sign ISO '{iso}'."
                raise InstallerError(msg) from e

        if self.stage == "upload":
            remote_path = self.config.repository_upload_remote_host.get(
                "iso", None
            )
            if not remote_path:
                self.log.info(
                    f"{self.dist}: No remote location defined. Skipping."
                )
                return

            try:
                cmd = [
                    f"rsync --partial --progress --hard-links -OJair --mkpath -- {iso_dir}/ {remote_path}"
                ]
                self.executor.run(cmd)
            except ExecutorError as e:
                raise InstallerError(
                    f"{self.dist}: Failed to upload to remote host: {str(e)}"
                ) from e
