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
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from dateutil.parser import parse as parsedate

from qubesbuilder.config import (
    Config,
    QUBES_RELEASE_RE,
    QUBES_RELEASE_DEFAULT,
    ConfigError,
)
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.log import QubesBuilderLogger
from qubesbuilder.plugins import (
    PluginError,
    TemplatePlugin,
    PluginDependency,
    JobDependency,
    JobReference,
    ComponentDependency,
)
from qubesbuilder.template import QubesTemplate

TEMPLATE_REPOSITORIES = [
    "templates-itl-testing",
    "templates-community-testing",
    "templates-itl",
    "templates-community",
]


class TemplateError(PluginError):
    pass


class TemplateBuilderPlugin(TemplatePlugin):
    """
    TemplatePlugin manages generic distribution release.

    Stages:
        - prep - Create root image and qubeized image.
        - build - Create template RPM from qubeized image.
        - sign - Sign template RPM.
        - publish - Create repository to be published and uploaded to remote mirror.
        - upload - Upload published repository for given distribution to remote mirror.
    """

    _publish_not_configured_warned = False

    @classmethod
    def supported_template(cls, template: QubesTemplate):
        return any(
            [
                template.distribution.is_rpm(),
                template.distribution.is_deb(),
                template.distribution.is_archlinux(),
                template.distribution.is_ubuntu(),
                template.distribution.is_gentoo(),
            ]
        )

    name = "template"

    def __init__(
        self,
        template: QubesTemplate,
        config: Config,
        stage: str,
        **kwargs,
    ):
        super().__init__(
            template=template,
            config=config,
            stage=stage,
            **kwargs,
        )
        self.template_version = ""

        self.update_parameters(self.stage)

        self.dependencies.append(PluginDependency("publish"))
        if stage == "build":
            self.dependencies.append(
                JobDependency(
                    JobReference(
                        component=None,
                        dist=self.dist,
                        stage="prep",
                        build=None,
                        template=template,
                    )
                )
            )

        if stage == "sign":
            self.dependencies.append(
                JobDependency(
                    JobReference(
                        component=None,
                        dist=self.dist,
                        stage="build",
                        build=None,
                        template=template,
                    )
                )
            )

    @classmethod
    def from_args(cls, **kwargs):
        config = kwargs.get("config")
        stage = kwargs.get("stage")
        template = kwargs.get("template")
        if config and template and stage in ("publish", "upload"):
            repository_publish = config.repository_publish.get("templates")
            if not repository_publish:
                if not cls._publish_not_configured_warned:
                    QubesBuilderLogger.info(
                        f"{cls.name}:{template}: "
                        f"'repository-publish:templates' not set."
                    )
                    cls._publish_not_configured_warned = True
                return None

        return super().from_args(**kwargs)

    def get_template_version(self):
        if not self.template_version:
            try:
                parsed_release = self.config.parse_qubes_release()
            except ConfigError as e:
                raise TemplateError(
                    f"Cannot parse template version: {str(e)}"
                ) from e
            # For now, we assume 4.X.0
            self.template_version = f"{parsed_release.group(1)}.0"
        return self.template_version

    def update_parameters(self, stage: str):
        template_options = [self.template.flavor] + self.template.options
        template_flavor_dir = []
        parsed_release = QUBES_RELEASE_RE.match(
            self.config.qubes_release
        ) or QUBES_RELEASE_RE.match(QUBES_RELEASE_DEFAULT)
        if not parsed_release:
            raise TemplateError(f"Cannot parse template version.")
        self.environment.update(
            {
                "DIST": self.dist.name,  # legacy value
                "DISTRIBUTION": self.dist.fullname,  # legacy value
                "DIST_CODENAME": self.dist.name,  # DIST
                "DIST_NAME": self.dist.fullname,  # DISTRIBUTION
                "DIST_VER": str(self.dist.version),
                "TEMPLATE_NAME": self.template.name,
                "TEMPLATE_VERSION": self.get_template_version(),
                "TEMPLATE_FLAVOR": self.template.flavor,
                "TEMPLATE_OPTIONS": " ".join(template_options),
                "INSTALL_DIR": f"{self.executor.get_builder_dir()}/mnt",
                "ARTIFACTS_DIR": str(self.executor.get_build_dir()),
                "PLUGINS_DIR": str(self.executor.get_plugins_dir()),
                "PACKAGES_DIR": str(self.executor.get_repository_dir()),
                "DISCARD_PREPARED_IMAGE": "1",
                "BUILDER_TURBO_MODE": "1",
                "CACHE_DIR": str(
                    self.executor.get_cache_dir() / f"cache_{self.dist.name}"
                ),
                "RELEASE": parsed_release.group(1),
                "TEMPLATE_SCRIPTS_DIR": str(
                    self.executor.get_plugins_dir() / "template/scripts"
                ),
            }
        )

        if self.config.template_root_size:
            self.environment.update(
                {"TEMPLATE_ROOT_SIZE": self.config.template_root_size}
            )

        if self.config.template_root_with_partitions:
            self.environment.update({"TEMPLATE_ROOT_WITH_PARTITIONS": "1"})

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

        mirrors = self.config.get("mirrors", {}).get(
            self.dist.distribution, []
        ) or self.config.get("mirrors", {}).get(self.dist.name, [])

        if self.template.distribution.is_rpm():
            component = self.config.get_components(["builder-rpm"])[0]
            self.dependencies += [
                PluginDependency("chroot_rpm"),
                PluginDependency("source_rpm"),
                ComponentDependency("builder-rpm"),
                JobDependency(
                    JobReference(
                        component=component,
                        stage="fetch",
                        build="source",
                        dist=None,
                        template=None,
                    )
                ),
            ]
            template_content_dir = str(
                self.executor.get_sources_dir() / "builder-rpm/template_rpm"
            )
            self.environment.update(
                {
                    "TEMPLATE_CONTENT_DIR": template_content_dir,
                    "KEYS_DIR": str(
                        self.executor.get_plugins_dir() / "chroot_rpm/keys"
                    ),
                }
            )
        elif (
            self.template.distribution.is_deb()
            or self.template.distribution.is_ubuntu()
        ):
            component = self.config.get_components(["builder-debian"])[0]
            self.dependencies += [
                PluginDependency("chroot_deb"),
                PluginDependency("source_deb"),
                PluginDependency("build_deb"),
                ComponentDependency("builder-debian"),
                JobDependency(
                    JobReference(
                        component=component,
                        stage="fetch",
                        build="source",
                        dist=None,
                        template=None,
                    )
                ),
            ]
            template_content_dir = str(
                self.executor.get_sources_dir()
                / f"builder-debian/template_{self.dist.fullname}"
            )
            self.environment.update(
                {
                    "TEMPLATE_CONTENT_DIR": template_content_dir,
                    "KEYS_DIR": str(
                        self.executor.get_plugins_dir() / "chroot_deb/keys"
                    ),
                }
            )
            self.environment.update({"DEBIAN_MIRRORS": " ".join(mirrors)})
            if self.template.flavor in (
                "whonix-gateway",
                "whonix-workstation",
            ):
                component = self.config.get_components(["template-whonix"])[0]
                self.dependencies += [
                    ComponentDependency("template-whonix"),
                    JobDependency(
                        JobReference(
                            component=component,
                            stage="fetch",
                            build="source",
                            dist=None,
                            template=None,
                        )
                    ),
                ]
                template_content_dir = str(
                    self.executor.get_sources_dir() / "template-whonix"
                )
                self.environment.update(
                    {
                        "TEMPLATE_ENV_WHITELIST": "DERIVATIVE_APT_REPOSITORY_OPTS WHONIX_ENABLE_TOR WHONIX_TBB_VERSION",
                        "APPMENUS_DIR": template_content_dir,
                        "FLAVORS_DIR": template_content_dir,
                        # FIXME: Pass values with the help of plugin options
                        "DERIVATIVE_APT_REPOSITORY_OPTS": "stable",
                        "WHONIX_ENABLE_TOR": "0",
                    }
                )
                template_flavor_dir += [
                    f"+whonix-gateway:{self.executor.get_sources_dir()}/template-whonix",
                    f"+whonix-workstation:{self.executor.get_sources_dir()}/template-whonix",
                ]
            if self.template.flavor in ("kicksecure",):
                component = self.config.get_components(["template-kicksecure"])[
                    0
                ]
                self.dependencies += [
                    ComponentDependency("template-kicksecure"),
                    JobDependency(
                        JobReference(
                            component=component,
                            stage="fetch",
                            build="source",
                            dist=None,
                            template=None,
                        )
                    ),
                ]
                template_content_dir = str(
                    self.executor.get_sources_dir() / "template-kicksecure"
                )
                self.environment.update(
                    {
                        "TEMPLATE_ENV_WHITELIST": "DERIVATIVE_APT_REPOSITORY_OPTS",
                        "APPMENUS_DIR": template_content_dir,
                        "FLAVORS_DIR": template_content_dir,
                        "DERIVATIVE_APT_REPOSITORY_OPTS": "stable",
                    }
                )
            if self.template.flavor.startswith("kali"):
                component = self.config.get_components(["template-kali"])[0]
                self.dependencies += [
                    ComponentDependency("template-kali"),
                    JobDependency(
                        JobReference(
                            component=component,
                            stage="fetch",
                            build="source",
                            dist=None,
                            template=None,
                        )
                    ),
                ]
                template_content_dir = str(
                    self.executor.get_sources_dir() / "template-kali"
                )
                self.environment.update(
                    {
                        "APPMENUS_DIR": template_content_dir,
                        "FLAVORS_DIR": template_content_dir,
                    }
                )
                template_flavor_dir += [
                    f"+kali:{self.executor.get_sources_dir()}/template-kali",
                    f"+kali-core:{self.executor.get_sources_dir()}/template-kali",
                    f"+kali-large:{self.executor.get_sources_dir()}/template-kali",
                    f"+kali-everything:{self.executor.get_sources_dir()}/template-kali",
                ]

        elif self.template.distribution.is_archlinux():
            component = self.config.get_components(["builder-archlinux"])[0]
            self.dependencies += [
                PluginDependency("chroot_archlinux"),
                ComponentDependency("builder-archlinux"),
                JobDependency(
                    JobReference(
                        component=component,
                        stage="fetch",
                        build="source",
                        dist=None,
                        template=None,
                    )
                ),
            ]
            template_content_dir = str(
                self.executor.get_sources_dir()
                / "builder-archlinux/template_archlinux"
            )
            self.environment.update(
                {
                    "TEMPLATE_CONTENT_DIR": template_content_dir,
                    "CACHE_DIR": str(self.executor.get_cache_dir()),
                    # We would use chroot_archlinux when deprecating legacy builder.
                    "KEYS_DIR": str(
                        self.executor.get_sources_dir()
                        / "builder-archlinux/keys"
                    ),
                }
            )
            self.environment.update({"ARCHLINUX_MIRROR": ",".join(mirrors)})
        elif self.template.distribution.is_gentoo():
            component = self.config.get_components(["builder-gentoo"])[0]
            self.dependencies += [
                ComponentDependency("builder-gentoo"),
                JobDependency(
                    JobReference(
                        component=component,
                        stage="fetch",
                        build="source",
                        dist=None,
                        template=None,
                    )
                ),
            ]
            template_content_dir = str(
                self.executor.get_sources_dir() / "builder-gentoo/scripts"
            )
            self.environment.update(
                {
                    "TEMPLATE_CONTENT_DIR": template_content_dir,
                    "CACHE_DIR": str(self.executor.get_cache_dir()),
                    "KEYS_DIR": str(
                        self.executor.get_sources_dir() / "builder-gentoo/keys"
                    ),
                }
            )
        else:
            raise TemplateError("Unsupported template.")
        template_flavor_dir += [
            f"+{option}:{template_content_dir}/{option}"
            for option in template_options
        ]
        self.environment["TEMPLATE_FLAVOR_DIR"] = " ".join(template_flavor_dir)

    def create_repository_skeleton(self):
        # Create publish repository skeleton
        artifacts_dir = self.config.repository_publish_dir / "rpm"

        create_skeleton_cmd = [
            f"{self.manager.entities['publish_rpm'].directory}/scripts/create-skeleton",
            self.config.qubes_release,
            "''",
            "''",
            str(artifacts_dir.absolute()),
            "''",
        ]
        cmd = [" ".join(create_skeleton_cmd)]
        try:
            self.executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.template}: Failed to create repository skeleton."
            raise TemplateError(msg) from e

    def create_and_sign_repository_metadata(self, repository_publish):
        executor = self.config.get_executor_from_config("publish", self)
        artifacts_dir = self.config.repository_publish_dir / "rpm"
        target_dir = (
            artifacts_dir / f"{self.config.qubes_release}/{repository_publish}"
        )

        (target_dir / "repodata").mkdir(parents=True, exist_ok=True)

        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("rpm", None)

        if not sign_key:
            self.log.info(f"{self.template}: No signing key found.")
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            self.log.info(f"{self.template}: Please specify GPG client to use!")
            return

        # Createrepo unpublished RPMs
        self.createrepo(executor=executor, target_dir=target_dir)

        # Sign metadata
        self.sign_metadata(
            executor=executor, sign_key=sign_key, target_dir=target_dir
        )

        # Create metalink
        self.create_metalink(
            executor=executor, repository_publish=repository_publish
        )

    def get_sign_key(self):
        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("rpm", None)

        if not sign_key:
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            raise TemplateError(
                f"{self.template}: Please specify GPG client to use!"
            )

        return sign_key

    def createrepo(self, executor, target_dir):
        self.log.info(f"{self.template}: Updating metadata.")
        cmd = [f"cd {target_dir}", "createrepo_c ."]
        try:
            shutil.rmtree(target_dir / "repodata")
            self.executor.run(cmd)
        except (ExecutorError, OSError) as e:
            msg = f"{self.template}: Failed to 'createrepo_c'"
            raise TemplateError(msg) from e

    def sign_metadata(self, executor, sign_key, target_dir):
        self.log.info(f"{self.template}: Signing metadata.")
        repomd = target_dir / "repodata/repomd.xml"
        cmd = [
            f"{self.config.gpg_client} --batch --no-tty --yes --detach-sign --armor -u {sign_key} {repomd} > {repomd}.asc",
        ]
        try:
            self.executor.run(cmd)
        except (ExecutorError, OSError) as e:
            msg = f"{self.template}: Failed to sign metadata"
            raise TemplateError(msg) from e

    @staticmethod
    def validate_repository_publish(repository_publish):
        if repository_publish not in TEMPLATE_REPOSITORIES:
            raise TemplateError(
                f"Invalid repository for template: '{repository_publish}'"
            )

    def is_published(self, repository):
        publish_info = self.get_template_artifacts_info("publish")
        if not publish_info:
            return False
        if publish_info["timestamp"] != self.get_template_timestamp():
            return False
        return repository in [
            r["name"] for r in publish_info.get("repository-publish", [])
        ]

    def can_be_published_in_stable(self, repository_publish, ignore_min_age):
        # Check template is published in testing: it is expected templates-itl-testing or
        # templates-community-testing.
        if not self.is_published(f"{repository_publish}-testing"):
            return False

        # Check minimum day that packages are available for testing
        publish_info = self.get_template_artifacts_info("publish")
        publish_date = None
        for r in publish_info["repository-publish"]:
            if r["name"] == f"{repository_publish}-testing":
                publish_date = datetime.datetime.strptime(
                    r["timestamp"] + "Z", "%Y%m%d%H%M%z"
                )
                break

        if publish_date is None:
            raise TemplateError(
                "Something wrong detected in repositories. Missing timestamp?"
            )

        # Check that packages have been published before threshold_date
        threshold_date = datetime.datetime.now(
            datetime.UTC
        ) - datetime.timedelta(days=self.config.min_age_days)
        if not ignore_min_age and publish_date > threshold_date:
            return False

        return True

    def get_template_tag(self):
        return f"{self.get_template_version()}-{self.get_template_timestamp()}"

    def create_metalink(self, executor, repository_publish):
        repo_basedir = self.config.repository_publish_dir / "rpm"
        repository_dir = (
            repo_basedir / self.config.qubes_release / repository_publish
        )
        repomd = repository_dir / "repodata/repomd.xml"
        if not repomd.exists():
            msg = f"{self.template.name}: Cannot find repomd '{repomd}'."
            raise TemplateError(msg)

        self.log.info(f"Creating metalink for {repomd}.")
        try:
            # XXX: consider separate mirrors.list?
            cmd = [
                f"python3 {self.manager.entities['publish'].directory}/mirrors/qubesmirror/metalink.py -b {repo_basedir} -- {self.manager.entities['publish_rpm'].directory}/mirrors.list {repomd} > {repomd}.metalink"
            ]
            self.executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.template.name}: Failed to create metalink for '{repomd}': {str(e)}."
            self.log.error(msg)

    def publish(self, executor, db_path, repository_publish):
        rpm = (
            self.config.templates_dir
            / "rpm"
            / f"qubes-template-{self.template.name}-{self.get_template_tag()}.noarch.rpm"
        )
        if not rpm.exists():
            msg = f"{self.template}: Cannot find template RPM '{rpm}'."
            raise TemplateError(msg)

        # We check that signature exists (--check-only option)
        self.log.info(f"{self.template}: Verifying signatures.")
        sign_key = self.get_sign_key()
        if not sign_key:
            self.log.info(f"{self.template}: No signing key found.")
            return
        try:
            cmd = [
                f"{self.manager.entities['sign_rpm'].directory}/scripts/sign-rpm "
                f"--sign-key {sign_key} --db-path {db_path} --rpm {rpm} --check-only"
            ]
            self.executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.template}: Failed to check signatures."
            raise TemplateError(msg) from e

        # Publish template with hardlinks to built RPM
        self.log.info(f"{self.template}: Publishing template.")
        artifacts_dir = self.config.repository_publish_dir / "rpm"
        target_dir = (
            artifacts_dir / f"{self.config.qubes_release}/{repository_publish}"
        )
        try:
            target_path = target_dir / "rpm" / rpm.name
            target_path.unlink(missing_ok=True)
            # target_path.hardlink_to(rpm)
            os.link(rpm, target_path)
        except (ValueError, PermissionError, NotImplementedError) as e:
            msg = f"{self.template}: Failed to publish template."
            raise TemplateError(msg) from e

        # Create and sign metadata
        self.create_and_sign_repository_metadata(repository_publish)

    def unpublish(self, executor, repository_publish):
        # Read information from build stage
        template_timestamp = self.get_template_timestamp()
        template_version = self.get_template_version()

        rpm = (
            self.config.templates_dir
            / "rpm"
            / f"qubes-template-{self.template.name}-{template_version}-{template_timestamp}.noarch.rpm"
        )
        if not rpm.exists():
            msg = f"{self.template}: Cannot find template RPM '{rpm}'."
            raise TemplateError(msg)

        sign_key = self.get_sign_key()
        if not sign_key:
            self.log.info(f"{self.template}: No signing key found.")
            return

        # If exists, remove hardlinks to built RPMs
        self.log.info(f"{self.template}: Unpublishing template.")
        artifacts_dir = self.config.repository_publish_dir / "rpm"
        target_dir = (
            artifacts_dir / f"{self.config.qubes_release}/{repository_publish}"
        )
        try:
            target_path = target_dir / "rpm" / rpm.name
            target_path.unlink(missing_ok=True)
        except (ValueError, PermissionError, NotImplementedError) as e:
            msg = f"{self.template}: Failed to unpublish template."
            raise TemplateError(msg) from e

        # Create and sign metadata
        self.create_and_sign_repository_metadata(repository_publish)

    def create(self, repository_publish: str):
        # Create skeleton
        self.create_repository_skeleton()

        # Create and sign metadata
        self.create_and_sign_repository_metadata(
            repository_publish=repository_publish
        )

    def run(
        self,
        repository_publish: Optional[str] = None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
        template_timestamp: Optional[str] = None,
        **kwargs,
    ):
        repository_dir = self.config.repository_dir / self.dist.distribution
        template_artifacts_dir = self.config.templates_dir
        qubeized_image = (
            template_artifacts_dir / "qubeized_images" / self.template.name
        )

        repository_dir.mkdir(parents=True, exist_ok=True)
        qubeized_image.mkdir(parents=True, exist_ok=True)

        #
        # Prep
        #

        if self.stage == "prep":
            if template_timestamp:
                template_timestamp = parsedate(template_timestamp).strftime(
                    "%Y%m%d%H%M"
                )
            else:
                template_timestamp = datetime.datetime.now(
                    datetime.UTC
                ).strftime("%Y%m%d%H%M")

            self.template.timestamp = template_timestamp
            self.environment.update({"TEMPLATE_TIMESTAMP": template_timestamp})

            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + [(repository_dir, self.executor.get_repository_dir())]

            copy_out = [
                (
                    self.executor.get_build_dir()
                    / "qubeized_images"
                    / self.template.name
                    / "root.img",
                    qubeized_image,
                ),
                (
                    self.executor.get_build_dir() / "appmenus",
                    template_artifacts_dir / self.template.name,
                ),
                (
                    self.executor.get_build_dir() / "template.conf",
                    template_artifacts_dir / self.template.name,
                ),
            ]

            cmd = [
                f"make -C {self.executor.get_plugins_dir()}/template prepare build-rootimg"
            ]
            try:
                self.executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                    dig_holes=True,
                )
            except ExecutorError as e:
                msg = f"{self.template}: Failed to prepare template."
                raise TemplateError(msg) from e

            # Save package information we built
            prep_info = {
                "timestamp": self.template.timestamp,
            }
            self.save_artifacts_info(
                self.stage,
                self.template.name,
                prep_info,
                self.config.templates_dir,
            )

        #
        # Build
        #

        if self.stage == "build":
            if not self.template.timestamp:
                self.template.timestamp = self.get_template_timestamp("prep")

            self.environment.update(
                {"TEMPLATE_TIMESTAMP": self.template.timestamp}
            )

            rpm_fn = f"qubes-template-{self.template.name}-{self.get_template_version()}-{self.template.timestamp}.noarch.rpm"

            copy_in = self.default_copy_in(
                self.executor.get_plugins_dir(), self.executor.get_sources_dir()
            ) + (
                [
                    (repository_dir, self.executor.get_repository_dir()),
                    (
                        qubeized_image / "root.img",
                        self.executor.get_build_dir()
                        / "qubeized_images"
                        / self.template.name,
                    ),
                    (
                        template_artifacts_dir
                        / self.template.name
                        / "template.conf",
                        self.executor.get_build_dir(),
                    ),
                    (
                        template_artifacts_dir
                        / self.template.name
                        / "appmenus",
                        self.executor.get_build_dir(),
                    ),
                ]
            )

            # Copy-in previously prepared base root img
            copy_out = [
                (
                    self.executor.get_build_dir()
                    / f"rpmbuild/RPMS/noarch/{rpm_fn}",
                    template_artifacts_dir / "rpm",
                ),
            ]

            cmd = [
                f"make -C {self.executor.get_plugins_dir()}/template prepare build-rpm"
            ]
            try:
                self.executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                )
            except ExecutorError as e:
                msg = f"{self.template}: Failed to build template."
                raise TemplateError(msg) from e

            # Save package information we built
            build_info = {
                "rpms": [str(rpm_fn)],
                "timestamp": self.template.timestamp,
            }
            self.save_artifacts_info(
                self.stage,
                self.template.name,
                build_info,
                self.config.templates_dir,
            )

        # Check that we have LocalExecutor for next stages
        if self.stage in ("sign", "publish", "upload") and not isinstance(
            self.executor, LocalExecutor
        ):
            raise TemplateError(
                f"This plugin only supports local executor for '{self.stage}' stage."
            )

        #
        # Sign
        #

        # Sign stage for templates
        if self.stage == "sign":
            db_path = template_artifacts_dir / "rpmdb"
            # We ensure to create a clean keyring for RPM
            if db_path.exists():
                shutil.rmtree(db_path)

            sign_key = self.get_sign_key()
            if not sign_key:
                self.log.info(f"{self.template}: No signing key found.")
                return

            temp_dir = Path(tempfile.mkdtemp())
            sign_key_asc = temp_dir / f"{sign_key}.asc"
            cmd = [
                f"mkdir -p {db_path}",
                f"{self.config.gpg_client} --armor --export {sign_key} > {sign_key_asc}",
                f"rpmkeys --dbpath={db_path} --import {sign_key_asc}",
            ]
            try:
                self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.template}: Failed to create RPM dbpath."
                raise TemplateError(msg) from e
            finally:
                # Clear temporary dir
                shutil.rmtree(temp_dir)

            template_timestamp = self.get_template_timestamp()
            template_version = self.get_template_version()

            rpm = (
                template_artifacts_dir
                / "rpm"
                / f"qubes-template-{self.template.name}-{template_version}-{template_timestamp}.noarch.rpm"
            )
            if not rpm.exists():
                msg = f"{self.template}: Cannot find template RPM '{rpm}'."
                raise TemplateError(msg)

            try:
                self.log.info(f"{self.template}: Signing '{rpm.name}'.")
                cmd = [
                    f"{self.manager.entities['sign_rpm'].directory}/scripts/sign-rpm "
                    f"--sign-key {sign_key} --db-path {db_path} --rpm {rpm}"
                ]
                self.executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.template}: Failed to sign template RPM '{rpm}'."
                raise TemplateError(msg) from e

        #
        # Publish
        #

        if self.stage in ("publish", "upload"):
            repository_publish = (
                repository_publish
                or self.config.repository_publish.get("templates")
            )
            if not repository_publish:
                raise TemplateError("Cannot determine repository for publish")

        # Publish stage for template components
        if self.stage == "publish" and not unpublish:
            self.validate_repository_publish(repository_publish)

            if self.is_published(repository_publish):
                self.log.info(
                    f"{self.template}: Already published to '{repository_publish}'."
                )
                return

            if repository_publish in (
                "templates-itl",
                "templates-community",
            ) and not self.can_be_published_in_stable(
                repository_publish, ignore_min_age
            ):
                failure_msg = (
                    f"{self.template}: "
                    f"Refusing to publish to '{repository_publish}' as template is not uploaded "
                    f"to '{repository_publish}-testing' for at least {self.config.min_age_days} days."
                )
                self.log.critical(failure_msg)
                return

            # Ensure dbpath from sign stage (still) exists
            db_path = template_artifacts_dir / "rpmdb"
            if not db_path.exists():
                msg = f"{self.template}: Failed to find RPM DB path."
                raise TemplateError(msg)

            # Create skeleton
            self.create_repository_skeleton()

            publish_info = self.get_template_artifacts_info(stage=self.stage)
            build_info = self.get_template_artifacts_info(stage="build")
            if not (
                publish_info
                and publish_info.get("timestamp", None)
                == self.get_template_timestamp()
            ):
                publish_info = build_info

            self.publish(
                executor=self.executor,
                db_path=db_path,
                repository_publish=repository_publish,
            )

            publish_info.setdefault("repository-publish", [])
            publish_info["repository-publish"].append(
                {
                    "name": repository_publish,
                    "timestamp": datetime.datetime.now(datetime.UTC).strftime(
                        "%Y%m%d%H%M"
                    ),
                }
            )
            # Save package information we published for committing into current
            self.save_artifacts_info(
                self.stage,
                self.template.name,
                publish_info,
                self.config.templates_dir,
            )

        if self.stage == "publish" and unpublish:
            if not self.is_published(repository_publish):
                self.log.info(
                    f"{self.template}: Not published to '{repository_publish}'."
                )
                return

            publish_info = self.get_template_artifacts_info(stage=self.stage)
            self.unpublish(
                executor=self.executor,
                repository_publish=repository_publish,
            )

            # Save package information we published for committing into current. If the packages
            # are not published into another repository, we delete the "publish" stage information.
            publish_info["repository-publish"] = [
                r
                for r in publish_info.get("repository-publish", [])
                if r["name"] != repository_publish
            ]
            if publish_info.get("repository-publish", []):
                self.save_artifacts_info(
                    self.stage,
                    self.template.name,
                    publish_info,
                    self.config.templates_dir,
                )
            else:
                self.log.info(
                    f"{self.template}: Not published anywhere else, deleting publish info."
                )
                self.delete_artifacts_info(stage="publish")

        if self.stage == "upload":
            remote_path = self.config.repository_upload_remote_host.get(
                "rpm", None
            )
            if not remote_path:
                self.log.info(
                    f"{self.dist}: No remote location defined. Skipping."
                )
                return

            try:
                local_path = (
                    self.config.repository_publish_dir
                    / "rpm"
                    / self.config.qubes_release
                )
                # Repository dir relative to local path that will be the same on remote host
                directories_to_upload = [repository_publish]

                if not directories_to_upload:
                    raise TemplateError(
                        f"{self.dist}: Cannot determine directories to upload."
                    )

                for relative_dir in directories_to_upload:
                    cmd = [
                        f"rsync --partial --progress --hard-links -OJair --mkpath -- {local_path / relative_dir}/ {remote_path}/{relative_dir}/"
                    ]
                    self.executor.run(cmd)
            except ExecutorError as e:
                raise TemplateError(
                    f"{self.dist}: Failed to upload to remote host: {str(e)}"
                ) from e


PLUGINS = [TemplateBuilderPlugin]
