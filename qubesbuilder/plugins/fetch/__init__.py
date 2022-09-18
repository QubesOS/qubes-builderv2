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

import os.path
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.exc import NoQubesBuilderFileError
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.log import get_logger
from qubesbuilder.plugins import ComponentPlugin, PluginError

log = get_logger("fetch")


class FetchError(PluginError):
    pass


class FetchPlugin(ComponentPlugin):
    """
    FetchPlugin manages generic fetch source

    Stages:
        - fetch - Downloads and verify external files, create submodule archives.

    Entry points:
        - source
    """

    stages = ["fetch"]

    def __init__(
        self,
        component: QubesComponent,
        config: Config,
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(component=component, config=config, manager=manager)

    def update_parameters(self, stage: str):
        """
        Update plugin parameters based on component .qubesbuilder.
        """
        super().update_parameters(stage)

        # Set and update parameters based on top-level "source"
        try:
            parameters = self.component.get_parameters(self.get_placeholders(stage))
        except NoQubesBuilderFileError:
            return
        self._parameters.update(parameters.get("source", {}))

    def run(self, stage: str):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run(stage=stage)

        if stage != "fetch":
            return

        executor = self.config.get_executor_from_config(stage)

        # Source component directory
        local_source_dir = self.get_sources_dir() / self.component.name

        # Ensure "artifacts/sources" directory exists
        self.get_sources_dir().mkdir(parents=True, exist_ok=True)

        # Ensure "artifacts/tmp" directory exists
        self.get_temp_dir().mkdir(exist_ok=True)

        # Source component directory inside executors
        source_dir = executor.get_builder_dir() / self.component.name
        copy_in = [
            (self.manager.entities["fetch"].directory, executor.get_plugins_dir())
        ]

        # Get GIT source for a given Qubes OS component
        copy_out = [(source_dir, self.get_sources_dir())]
        get_sources_cmd = [
            str(executor.get_plugins_dir() / "fetch/scripts/get-and-verify-source"),
            "--component",
            self.component.name,
            "--git-branch",
            self.component.branch,
            "--git-url",
            self.component.url,
            "--keyring-dir-git",
            str(executor.get_builder_dir() / "keyring"),
            "--keys-dir",
            str(executor.get_plugins_dir() / "fetch/keys"),
        ]
        for maintainer in self.component.maintainers:
            get_sources_cmd += ["--maintainer", maintainer]
        if self.component.insecure_skip_checking:
            get_sources_cmd += ["--insecure-skip-checking"]
        if self.component.less_secure_signed_commits_sufficient:
            get_sources_cmd += ["--less-secure-signed-commits-sufficient"]

        # We prioritize versions first
        if local_source_dir.exists():
            if self.config.fetch_versions_only:
                get_sources_cmd += ["--fetch-versions-only"]

        do_fetch = True
        if local_source_dir.exists():
            # If we already fetched sources previously and have modified them,
            # we may want to keep local modifications.
            if self.config.force_fetch:
                shutil.rmtree(str(local_source_dir))
            elif self.config.skip_git_fetch:
                do_fetch = False
            else:
                log.info(f"{self.component}: source already fetched. Updating.")
                copy_in += [(local_source_dir, executor.get_builder_dir())]

        if do_fetch:
            cmd = [
                f"cd {str(executor.get_builder_dir())}",
                " ".join(get_sources_cmd),
            ]
            executor.run(cmd, copy_in, copy_out, environment=self.environment)

        # Update parameters based on previously fetched sources as .qubesbuilder
        # is now available.
        parameters = self.get_parameters(stage)

        distfiles_dir = self.get_component_distfiles_dir()
        distfiles_dir.mkdir(parents=True, exist_ok=True)

        # Download and verify files given in .qubesbuilder
        for file in parameters.get("files", []):
            # Temporary dir for downloaded file
            temp_dir = Path(tempfile.mkdtemp(dir=self.get_temp_dir()))

            #
            # download
            #
            parsed_url = urllib.parse.urlparse(file["url"])
            fn = str(os.path.basename(parsed_url.geturl()))

            # If we request to uncompress the file we drop the archive suffix
            if file.get("uncompress", False):
                final_fn = Path(fn).with_suffix("").name
            else:
                final_fn = fn

            untrusted_final_fn = "untrusted_" + final_fn

            if (distfiles_dir / final_fn).exists():
                if self.config.force_fetch:
                    os.remove(distfiles_dir / final_fn)
                else:
                    log.info(
                        f"{self.component}: file {final_fn} already downloaded. Skipping."
                    )
                    continue
            copy_in = [
                (self.manager.entities["fetch"].directory, executor.get_plugins_dir()),
                (self.component.source_dir, executor.get_builder_dir()),
            ]
            copy_out = [(source_dir / untrusted_final_fn, temp_dir)]

            # Construct command for "download-file".
            download_cmd = [
                str(executor.get_plugins_dir() / "fetch/scripts/download-file"),
                "--output-dir",
                str(executor.get_builder_dir() / self.component.name),
                "--file-name",
                fn,
                "--file-url",
                file["url"],
            ]
            if file.get("signature", None):
                download_cmd += ["--signature-url", file["signature"]]
                signature_fn = os.path.basename(file["signature"])
                untrusted_signature_fn = "untrusted_" + signature_fn
                copy_out += [
                    (
                        executor.get_builder_dir()
                        / self.component.name
                        / untrusted_signature_fn,
                        temp_dir,
                    )
                ]
            if file.get("uncompress", False):
                download_cmd += ["--uncompress"]
            cmd = [" ".join(download_cmd)]
            try:
                executor.run(cmd, copy_in, copy_out, environment=self.environment)
            except ExecutorError as e:
                shutil.rmtree(temp_dir)
                raise FetchError(f"Failed to download file '{file}': {str(e)}.")

            #
            # verify
            #

            # Keep executor workflow if we move verification of files in another
            # cage type (copy-in, copy-out and cmd would need adjustments).

            if isinstance(executor, LocalExecutor):
                # If executor is a LocalExecutor, use the same base
                # directory for temporary directory
                local_executor = LocalExecutor(directory=executor.get_directory())
            else:
                local_executor = LocalExecutor()

            copy_in = []
            copy_out = [(temp_dir / final_fn, distfiles_dir)]

            # Construct command for "verify-file".
            verify_cmd = [
                str(self.manager.entities["fetch"].directory / "scripts/verify-file"),
                "--output-dir",
                str(temp_dir),
                "--untrusted-file",
                str(temp_dir / untrusted_final_fn),
            ]
            if file.get("sha256", None):
                verify_cmd += [
                    "--checksum-cmd",
                    "sha256sum",
                    "--checksum-file",
                    str(self.component.source_dir / file["sha256"]),
                ]
            elif file.get("sha512", None):
                verify_cmd += [
                    "--checksum-cmd",
                    "sha512sum",
                    "--checksum-file",
                    str(self.component.source_dir / file["sha512"]),
                ]
            elif file.get("signature", None):
                signature_fn = os.path.basename(file["signature"])
                untrusted_signature_fn = "untrusted_" + signature_fn
                verify_cmd += [
                    "--untrusted-signature-file",
                    str(temp_dir / untrusted_signature_fn),
                ]
                copy_out += [
                    (
                        temp_dir / signature_fn,
                        distfiles_dir,
                    )
                ]
            else:
                raise FetchError(f"No verification method for {final_fn}")

            if file.get("pubkeys", None):
                for pubkey in file["pubkeys"]:
                    verify_cmd += [
                        "--pubkey-file",
                        str(self.component.source_dir / pubkey),
                    ]

            cmd = [" ".join(verify_cmd)]
            try:
                local_executor.run(cmd, copy_in, copy_out, environment=self.environment)
            except ExecutorError as e:
                raise FetchError(f"Failed to verify file '{file}': {str(e)}.")
            finally:
                shutil.rmtree(temp_dir)

        #
        # source hash and version tags determination
        #

        # Temporary directory
        temp_dir = Path(tempfile.mkdtemp(dir=self.get_temp_dir()))

        # Source component directory inside executors
        source_dir = executor.get_builder_dir() / self.component.name

        # We store the fetched source hash as original reference to be compared
        # for any further modifications. Once the source is fetched, we may locally
        # modify the source for development and at prep stage we would need to recompute
        # source hash based on those modifications.
        info: dict[str, Any] = {"source-hash": self.component.get_source_hash()}

        # Get git hash and tags
        copy_in = [
            (self.component.source_dir, executor.get_builder_dir()),
        ]
        copy_out = [
            (source_dir / "hash", temp_dir),
            (source_dir / "vtags", temp_dir),
        ]
        cmd = [
            f"rm -f {source_dir}/hash {source_dir}/vtags",
            f"cd {executor.get_builder_dir()}",
        ]
        cmd += [f"git -C {source_dir} rev-parse 'HEAD^{{}}' >> {source_dir}/hash"]
        cmd += [
            f"git -C {source_dir} tag --points-at HEAD --list 'v*' >> {source_dir}/vtags"
        ]
        try:
            executor.run(cmd, copy_in, copy_out, environment=self.environment)
        except ExecutorError as e:
            msg = f"{self.component}: Failed to get source hash information: {e}."
            raise FetchError(msg) from e

        # Read git hash and vtags
        with open(temp_dir / "hash") as f:
            data = f.read().splitlines()

        if not re.match(r"[\da-f]{40}", data[0]):
            msg = f"{self.component}: Invalid git hash detected."
            raise FetchError(msg)

        info["git-commit-hash"] = data[0]
        info["git-version-tags"] = []

        with open(temp_dir / "vtags") as f:
            data = f.read().splitlines()

        for tag in data:
            if not re.match("^v.*", tag):
                msg = f"{self.component}: Invalid git version tag detected."
                raise FetchError(msg)
            info["git-version-tags"].append(tag)

        # Modules (formerly known as INCLUDED_SOURCES in Makefile.builder)
        modules = parameters.get("modules", [])
        if modules:
            # Get git module hashes
            copy_in = [
                (self.component.source_dir, executor.get_builder_dir()),
            ]
            copy_out = [(source_dir / "modules", temp_dir)]
            cmd = [
                f"rm -f {source_dir}/modules",
                f"cd {executor.get_builder_dir()}",
            ]
            for module in modules:
                cmd += [
                    f"git -C {source_dir}/{module} rev-parse HEAD >> {source_dir}/modules"
                ]
            try:
                executor.run(cmd, copy_in, copy_out, environment=self.environment)
            except ExecutorError as e:
                msg = f"{self.component}: Failed to get source module information: {str(e)}."
                raise FetchError(msg) from e

            # Read package release name
            with open(temp_dir / "modules") as f:
                data = f.read().splitlines()
            if len(data) != len(modules):
                msg = f"{self.component}: Invalid modules data."
                raise FetchError(msg)

            for commit_hash in data:
                if not re.match("[0-9a-f]{40}", commit_hash):
                    msg = f"{self.component}: Invalid module hash detected."
                    raise FetchError(msg)

            info.update(
                {
                    "modules": [
                        {"name": name, "hash": str(data[idx])}
                        for idx, name in enumerate(modules)
                    ],
                }
            )

            copy_in = [
                (self.component.source_dir, executor.get_builder_dir()),
                (self.manager.entities["fetch"].directory, executor.get_plugins_dir()),
            ]
            copy_out = []
            cmd = []
            for module in info["modules"]:
                module["archive"] = f"{module['name']}-{module['hash'][0:16]}.tar.gz"
                copy_out += [
                    (
                        source_dir / module["name"] / module["archive"],
                        distfiles_dir,
                    ),
                ]
                cmd += [
                    f"{executor.get_plugins_dir()}/fetch/scripts/create-archive {source_dir}/{module['name']} {module['archive']} {module['name']}/",
                ]

            try:
                executor.run(cmd, copy_in, copy_out, environment=self.environment)
            except ExecutorError as e:
                msg = f"{self.component}: Failed to generate module archives: {str(e)}."
                raise FetchError(msg) from e

        try:
            self.save_artifacts_info(stage=stage, basename="source", info=info)
            # Clean temp_dir
            shutil.rmtree(temp_dir)
        except OSError as e:
            msg = f"{self.component}: Failed to clean artifacts: {str(e)}."
            raise FetchError(msg) from e


PLUGINS = [FetchPlugin]
