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
import shlex
import shutil
import tempfile
import urllib.parse
from pathlib import Path
from shlex import quote
from typing import Any, List, Union

from qubesbuilder.common import VerificationMode, get_archive_name
from qubesbuilder.exc import NoQubesBuilderFileError
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.plugins import ComponentPlugin, PluginError


class FetchError(PluginError):
    pass


def quote_list(args: List[Union[str, Path]]) -> str:
    return " ".join(map(lambda x: quote(str(x)), args))


class FetchPlugin(ComponentPlugin):
    """
    FetchPlugin manages generic fetch source

    Stages:
        - fetch - Downloads and verify external files, create submodule archives.

    Entry points:
        - source
    """

    name = "fetch"
    stages = ["fetch"]

    def update_parameters(self, stage: str):
        """
        Update plugin parameters based on component .qubesbuilder.
        """
        super().update_parameters(stage)

        # Set and update parameters based on top-level "source"
        try:
            parameters = self.component.get_parameters(
                self.get_placeholders(stage)
            )
        except NoQubesBuilderFileError:
            return
        self._parameters[stage].update(parameters.get("source", {}))

    def run(self):
        """
        Run plugin for given stage.
        """

        # Override provided executor for git phase only
        if self.config.get("git-run-inplace", False):
            executor = LocalExecutor()
            executor.log = self.log.getChild(self.stage)
        else:
            executor = self.executor

        # Source component directory
        local_source_dir = self.config.sources_dir / self.component.name

        # Ensure "artifacts/sources" directory exists
        self.config.sources_dir.mkdir(parents=True, exist_ok=True)

        # Ensure "artifacts/tmp" directory exists
        self.config.temp_dir.mkdir(exist_ok=True)

        # Source component directory inside executors
        source_dir = executor.get_builder_dir() / self.component.name
        copy_in = self.default_copy_in(
            executor.get_plugins_dir(), executor.get_sources_dir()
        )
        for key_dir_str in self.config.get("key-dirs", []):
            key_dir = Path(key_dir_str)
            if not key_dir.is_absolute():
                key_dir = self.config.get_conf_path().parent.joinpath(key_dir)
            if not key_dir.is_dir():
                self.log.warn(f"Key directory '{key_dir!s}' is not a directory")
                continue
            for key_file in key_dir.iterdir():
                if key_file.suffix != ".asc":
                    continue
                copy_in += [
                    (
                        key_file,
                        executor.get_plugins_dir() / "fetch/keys",
                    )
                ]

        # Get GIT source for a given Qubes OS component
        get_sources_cmd = [
            str(
                executor.get_plugins_dir()
                / "fetch/scripts/get-and-verify-source.py"
            ),
            self.component.url,  # clone from
            str(source_dir),  # clone into
            str(executor.get_builder_dir() / "keyring"),  # git keyring dir
            str(
                executor.get_plugins_dir() / "fetch/keys"
            ),  # keys for maintainers
            "--git-branch",
            self.component.branch,
            "--minimum-distinct-maintainers",
            str(self.component.min_distinct_maintainers),
        ]
        for maintainer in self.component.maintainers:
            get_sources_cmd += ["--maintainer", maintainer]
        if self.component.verification_mode == VerificationMode.Insecure:
            get_sources_cmd += ["--insecure-skip-checking"]
        elif self.component.verification_mode == VerificationMode.SignedCommit:
            get_sources_cmd += ["--less-secure-signed-commits-sufficient"]

        if self.component.fetch_versions_only:
            get_sources_cmd += ["--fetch-versions-only"]

        cmd = []
        copy_out = [(source_dir, self.config.sources_dir)]

        do_fetch = True
        if local_source_dir.exists():
            # If we already fetched sources previously and have modified them,
            # we may want to keep local modifications.
            if self.config.force_fetch:
                shutil.rmtree(str(local_source_dir))
            elif self.config.skip_git_fetch:
                do_fetch = False
            else:
                self.log.info(
                    f"{self.component}: source already fetched. Updating."
                )
                if self.config.get("git-run-inplace", False):
                    cmd += [f"ln -s {local_source_dir} {source_dir}"]
                    copy_out = []
                else:
                    copy_in += [(local_source_dir, executor.get_builder_dir())]

        if do_fetch:
            cmd += [
                f"cd {str(executor.get_builder_dir())}",
                " ".join(get_sources_cmd),
            ]
            executor.run(cmd, copy_in, copy_out, environment=self.environment)

        # Update parameters based on previously fetched sources as .qubesbuilder
        # is now available.
        super().run()

        executor = self.executor
        parameters = self.get_parameters(self.stage)
        distfiles_dir = self.get_component_distfiles_dir()
        distfiles_dir.mkdir(parents=True, exist_ok=True)

        # Download and verify files given in .qubesbuilder
        if not self.config.get("skip-files-fetch", False):
            for file in parameters.get("files", []):
                if "url" in file:
                    self.download_file(file, executor, distfiles_dir)
                elif "git-url" in file:
                    self.download_git_archive(file, executor, distfiles_dir)
                else:
                    msg = (
                        "'files' entries must have either url or git-url entry"
                    )
                    raise FetchError(msg)

        #
        # source hash and version tags determination
        #

        # Temporary directory
        temp_dir = Path(tempfile.mkdtemp(dir=self.config.temp_dir))

        # Keep existing fetch info if it is up-to-date
        source_hash = self.component.get_source_hash(force_update=True)
        old_info = self.get_artifacts_info(stage=self.stage, basename="source")
        if "source-hash" in old_info and old_info["source-hash"] == source_hash:
            return

        # We store the fetched source hash as original reference to be compared
        # for any further modifications. Once the source is fetched, we may locally
        # modify the source for development and at prep stage we would need to recompute
        # source hash based on those modifications.
        info: dict[str, Any] = {
            "source-hash": source_hash,
        }

        if self.config.get("git-run-inplace", False):
            executor = LocalExecutor()
            executor.log = self.log.getChild(self.stage)
        else:
            executor = self.executor

        # Source component directory inside executors
        source_dir = executor.get_builder_dir() / self.component.name

        # Get git hash and tags
        if self.config.get("git-run-inplace", False):
            cmd = [f"ln -s {local_source_dir} {source_dir}"]
            copy_in = []
        else:
            cmd = []
            copy_in = [(local_source_dir, executor.get_builder_dir())]
        copy_out = [
            (executor.get_builder_dir() / "hash", temp_dir),
            (executor.get_builder_dir() / "vtags", temp_dir),
        ]
        cmd += [
            quote_list(["cd", "--", executor.get_builder_dir()]),
            quote_list(["git", "-C", source_dir, "rev-parse", "HEAD^{}"])
            + " >> hash",
            quote_list(
                [
                    "git",
                    "-C",
                    source_dir,
                    "tag",
                    "--points-at",
                    "HEAD",
                    "--list",
                    "v*",
                ]
            )
            + " >> vtags",
        ]
        self.log.debug(cmd)
        try:
            executor.run(cmd, copy_in, copy_out, environment=self.environment)
        except ExecutorError as e:
            msg = (
                f"{self.component}: Failed to get source hash information: {e}."
            )
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
            if self.config.get("git-run-inplace", False):
                cmd = [f"ln -s {local_source_dir} {source_dir}"]
                copy_in = []
            else:
                cmd = []
                copy_in = [(local_source_dir, executor.get_builder_dir())]
            copy_out = [(executor.get_builder_dir() / "modules", temp_dir)]
            cmd += [
                quote_list(
                    ["rm", "-f", "--", executor.get_builder_dir() / "modules"]
                ),
                quote_list(["cd", "--", executor.get_builder_dir()]),
            ]
            for module in modules:
                cmd += [
                    quote_list(
                        ["git", "-C", source_dir / module, "rev-parse", "HEAD"]
                    )
                    + " >> modules",
                ]
            try:
                executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
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

            #
            # Create modules archives
            #

            executor = self.executor
            source_dir = executor.get_builder_dir() / self.component.name

            copy_in = [
                (self.component.source_dir, executor.get_builder_dir()),
                (
                    self.manager.entities["fetch"].directory,
                    executor.get_plugins_dir(),
                ),
            ]
            copy_out = []
            cmd = []
            for module in info["modules"]:
                module["archive"] = (
                    f"{module['name']}-{module['hash'][0:16]}.tar.gz"
                )
                copy_out += [
                    (
                        source_dir / module["name"] / module["archive"],
                        distfiles_dir,
                    ),
                ]
                cmd += [
                    quote_list(
                        [
                            f"{executor.get_plugins_dir()}/fetch/scripts/create-archive",
                            f"{source_dir}/{module['name']}",
                            f"{module['archive']}",
                            f"{module['name']}/",
                        ]
                    ),
                ]

            try:
                executor.run(
                    cmd, copy_in, copy_out, environment=self.environment
                )
            except ExecutorError as e:
                msg = f"{self.component}: Failed to generate module archives: {str(e)}."
                raise FetchError(msg) from e

        if self.config.increment_devel_versions:
            self.component.increment_devel_versions()

        try:
            self.save_artifacts_info(
                stage=self.stage, basename="source", info=info
            )
            # Clean temp_dir
            shutil.rmtree(temp_dir)
        except OSError as e:
            msg = f"{self.component}: Failed to clean or create artifacts: {str(e)}."
            raise FetchError(msg) from e

    def download_git_archive(self, file, executor, distfiles_dir):
        repo_bn = os.path.basename(file["git-url"]).partition(".git")[0]
        if "git-basename" in file:
            archive_base = file["git-basename"]
        else:
            archive_base = repo_bn
        if "tag" in file:
            if "/" in file["tag"]:
                msg = "Tags with '/' are not supported"
                raise FetchError(msg)
        elif "commit-id" in file:
            if not re.match(r"\A[a-z0-9]*\Z", file["commit-id"]) or len(
                file["commit-id"]
            ) not in (40, 64):
                msg = "Full commit id is needed for 'commit-id'"
                raise FetchError(msg)
        else:
            msg = "Fetching git archive requires either 'tag' or 'commit-id'"
            raise FetchError(msg)
        archive_name = get_archive_name(file)

        if (distfiles_dir / archive_name).exists():
            if self.config.force_fetch:
                os.remove(distfiles_dir / archive_name)
            else:
                self.log.info(
                    f"{self.component}: file {archive_name} already downloaded. Skipping."
                )
                return
        copy_in = [
            (
                self.manager.entities["fetch"].directory,
                executor.get_plugins_dir(),
            ),
        ]
        local_source_dir = self.config.sources_dir / self.component.name
        for key_file in file.get("pubkeys", []):
            copy_in += [
                (
                    local_source_dir / key_file,
                    executor.get_builder_dir() / "keys",
                )
            ]

        source_dir = executor.get_builder_dir() / repo_bn

        get_sources_cmd = [
            str(
                executor.get_plugins_dir()
                / "fetch/scripts/get-and-verify-source.py"
            ),
            "--shallow-clone",
            "--trust-all-keys",
            file["git-url"],  # clone from
            str(source_dir),  # clone into
            str(executor.get_builder_dir() / "keyring"),  # git keyring dir
            str(executor.get_builder_dir() / "keys"),  # keys to import
        ]
        if "tag" in file:
            get_sources_cmd += ["--git-branch", file["tag"]]
        elif "commit-id" in file:
            get_sources_cmd += ["--git-commit", file["commit-id"]]

        cmd = [
            f"cd {str(executor.get_builder_dir())}",
            " ".join(get_sources_cmd),
            f"{executor.get_plugins_dir()}/fetch/scripts/create-archive {source_dir} {archive_name} {archive_base}/",
        ]

        copy_out = [(source_dir / archive_name, distfiles_dir)]

        try:
            executor.run(cmd, copy_in, copy_out, environment=self.environment)
        except ExecutorError as e:
            raise FetchError(f"Failed to download file '{file}': {str(e)}.")

    def download_file(self, file, executor, distfiles_dir):
        # Temporary dir for downloaded file
        temp_dir = Path(tempfile.mkdtemp(dir=self.config.temp_dir))
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
                self.log.info(
                    f"{self.component}: file {final_fn} already downloaded. Skipping."
                )
                return
        copy_in = [
            (
                self.manager.entities["fetch"].directory,
                executor.get_plugins_dir(),
            ),
            (self.component.source_dir, executor.get_builder_dir()),
        ]
        source_dir = executor.get_builder_dir() / self.component.name
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
        cmd = [" ".join(map(shlex.quote, download_cmd))]
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
        local_executor.log = self.log.getChild("fetch")

        copy_in = []
        copy_out = [(temp_dir / final_fn, distfiles_dir)]
        # Construct command for "verify-file".
        verify_cmd = [
            str(
                self.manager.entities["fetch"].directory / "scripts/verify-file"
            ),
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
        cmd = [" ".join(map(shlex.quote, verify_cmd))]
        try:
            local_executor.run(
                cmd, copy_in, copy_out, environment=self.environment
            )
        except ExecutorError as e:
            raise FetchError(f"Failed to verify file '{file}': {str(e)}.")
        finally:
            shutil.rmtree(temp_dir)


PLUGINS = [FetchPlugin]
