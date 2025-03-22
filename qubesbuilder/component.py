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

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Union, List, TYPE_CHECKING

if TYPE_CHECKING:
    try:
        from _hashlib import HASH
    except ModuleNotFoundError:
        from _sha512 import sha512 as HASH  # type: ignore[no-redef]

import pathspec
import yaml

# pylint: disable=protected-access
from packaging.version import Version, InvalidVersion, VERSION_PATTERN, _Version

from qubesbuilder.common import sanitize_line, deep_check, VerificationMode
from qubesbuilder.exc import ComponentError, NoQubesBuilderFileError

# allow fractional post-release (like 1.0-0.1)
VERSION_PATTERN_REL = r"(?:(?P<post_frac>\.[0-9]+))?"


class QubesVersion(Version):
    """Version class that preserves '-' in X.Y-rcZ version,
    and allows decimal point in post-release
    """

    _regex = re.compile(
        r"^\s*" + VERSION_PATTERN + VERSION_PATTERN_REL + r"\s*$",
        re.VERBOSE | re.IGNORECASE,
    )

    def __init__(self, version: str) -> None:
        super().__init__(version)
        if "-rc" in version:
            # pylint: disable=protected-access
            self._version: _Version = self._version._replace(
                pre=("-rc", self._version.pre[1])
            )
        match = self._regex.search(version)
        assert match  # already verified in parent
        if match.group("post_frac"):
            self._version = self._version._replace(
                post=(
                    self._version.post[0],
                    str(self._version.post[1]) + match.group("post_frac"),
                )
            )


class QubesComponent:
    def __init__(
        self,
        source_dir: Union[str, Path],
        name: str = None,
        url: str = None,
        branch: str = "main",
        verification_mode: VerificationMode = VerificationMode.SignedCommit,
        maintainers: List = None,
        timeout: int = None,
        fetch_versions_only: bool = False,
        devel_path: Path = None,
        is_plugin: bool = False,
        has_packages: bool = True,
        min_distinct_maintainers: int = 1,
        **kwargs,
    ):
        self.source_dir: Path = (
            Path(source_dir) if isinstance(source_dir, str) else source_dir
        )
        self.name: str = name or self.source_dir.name
        self.version = ""
        self.release = ""
        self.devel = ""
        self.url = url or f"https://github.com/QubesOS/qubes-{self.name}"
        self.branch = branch
        self.maintainers = maintainers or []
        self.min_distinct_maintainers = min_distinct_maintainers
        self.verification_mode = verification_mode
        self.timeout = timeout
        self.fetch_versions_only = fetch_versions_only
        self.is_plugin = is_plugin
        self.has_packages = has_packages
        self._source_hash = ""
        self._devel_path = devel_path
        self.kwargs = kwargs

    def get_version_release(self):
        if self.is_plugin or not self.has_packages:
            version_release = "noversion"
        elif self.get_version() and self.get_release():
            version_release = f"{self.get_version()}-{self.get_release()}"
            if self.get_devel():
                version_release = f"{version_release}.{self.get_devel()}"
        else:
            raise ComponentError("Cannot determine version and release!")
        return version_release

    def increment_devel_versions(self):
        if not self.has_packages:
            return
        devel = "1"
        if not self._devel_path:
            raise ComponentError(f"Devel path not provided for {self.name}.")
        self._devel_path.parent.mkdir(parents=True, exist_ok=True)
        if self._devel_path.exists():
            try:
                with open(self._devel_path) as fd:
                    devel = fd.read().split("\n")[0]
                assert re.fullmatch(r"[0-9]+", devel)
                devel = str(int(devel) + 1)
            except AssertionError as e:
                raise ComponentError(
                    f"Invalid devel version for {self.name}."
                ) from e
        self._devel_path.write_text(devel)
        self.devel = devel

    def get_version(self):
        if self.version:
            return self.version
        version = ""
        version_file = self.source_dir / "version"
        if version_file.exists():
            try:
                with open(version_file) as fd:
                    version_str = fd.read().split("\n")[0]
                    # validate
                    QubesVersion(version_str)
                    # but use the original
                    version = version_str
            except InvalidVersion as e:
                raise ComponentError(
                    f"Invalid version for {self.source_dir}."
                ) from e
        else:
            result = subprocess.run(
                "git describe --match='v*' --abbrev=0",
                capture_output=True,
                shell=True,
                cwd=self.source_dir,
            )
            if result.stdout:
                version = sanitize_line(result.stdout.rstrip(b"\n")).rstrip()
                version_re = re.compile(r"v?([0-9]+(?:\.[0-9]+)*)-([0-9]+.*)")
                if len(version) > 255 or not version_re.match(version):
                    raise ComponentError(
                        f"Invalid version for {self.source_dir}."
                    )
                version, release = version_re.match(version).groups()  # type: ignore
        if not version:
            raise ComponentError(
                f"Cannot determine version for {self.source_dir}."
            )
        self.version = version
        return self.version

    def get_release(self):
        release_file = self.source_dir / "rel"
        if self.release:
            return self.release
        if not release_file.exists():
            release = "1"
        else:
            try:
                with open(release_file) as fd:
                    release = fd.read().split("\n")[0]
                QubesVersion(f"{self.get_version()}-{release}")
            except (InvalidVersion, AssertionError) as e:
                raise ComponentError(
                    f"Invalid release for {self.source_dir}."
                ) from e
        self.release = release
        return self.release

    def get_devel(self):
        if self._devel_path and self._devel_path.exists():
            try:
                with open(self._devel_path) as fd:
                    devel = fd.read().split("\n")[0]
                assert re.fullmatch(r"[0-9]+", devel)
                self.devel = devel
            except AssertionError as e:
                raise ComponentError(
                    f"Invalid devel version for {self.name}."
                ) from e
        return self.devel

    def get_parameters(self, placeholders: dict = None):
        if not self.source_dir.exists():
            raise ComponentError(
                f"Cannot find source directory {self.source_dir}."
            )

        build_file = self.source_dir / ".qubesbuilder"

        if self.is_plugin or not self.has_packages:
            return {}

        if not build_file.exists():
            raise NoQubesBuilderFileError(
                f"Cannot find '.qubesbuilder' in {self.source_dir}."
            )

        placeholders = placeholders or {}
        placeholders.update(
            {
                "@COMPONENT@": self.name,
                "@VERSION@": self.get_version(),
                "@REL@": self.get_release(),
                "@VERREL@": self.get_version_release(),
            }
        )

        with open(build_file) as f:
            data = f.read()

        for key, val in placeholders.items():
            data = data.replace(key, str(val))

        try:
            rendered_data = yaml.safe_load(data) or {}
        except yaml.YAMLError as e:
            raise ComponentError(f"Cannot render '.qubesbuilder'.") from e

        # TODO: add more extra validation of some field
        try:
            deep_check(rendered_data)
        except ValueError as e:
            raise ComponentError(f"Invalid '.qubesbuilder': {str(e)}")

        return rendered_data

    @staticmethod
    def _update_hash_from_file(filename: Path, hash: "HASH"):
        with open(str(filename), "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash.update(chunk)
        return hash

    def _update_hash_from_dir(self, directory: Path, hash: "HASH"):
        if not directory.exists() or not directory.is_dir():
            raise ComponentError(f"Cannot find '{directory}'.")
        paths = [name for name in Path(directory).iterdir()]
        excluded_paths = [directory / ".git"]
        # We ignore .git and content defined by .gitignore
        if (directory / ".gitignore").exists():
            lines = (directory / ".gitignore").read_text().splitlines()
            spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
            excluded_paths += [
                name for name in paths if spec.match_file(str(name))
            ]
        sorted_paths = [path for path in paths if path not in excluded_paths]
        # We ensure to compute hash always in a sorted order
        sorted_paths = sorted(sorted_paths, key=lambda p: str(p).lower())
        for path in sorted_paths:
            hash.update(path.name.encode())
            if path.is_file():
                hash = self._update_hash_from_file(path, hash)
            elif path.is_dir():
                hash = self._update_hash_from_dir(path, hash)
        return hash

    def get_source_hash(self, force_update=True):
        if not self._source_hash or force_update:
            source_dir_hash = self._update_hash_from_dir(
                self.source_dir, hashlib.sha512()
            ).hexdigest()
            self._source_hash = str(source_dir_hash)
        return self._source_hash

    def get_source_commit_hash(self):
        cmd = ["git", "-C", str(self.source_dir), "rev-parse", "HEAD^{}"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True
            )
            return result.stdout.strip("\n")
        except subprocess.CalledProcessError as e:
            raise ComponentError(
                f"Cannot determine source commit hash for {self.source_dir}."
            ) from e

    def is_salt(self):
        return (self.source_dir / "FORMULA").exists()

    def to_str(self) -> str:
        return self.source_dir.name

    def __repr__(self):
        return f"<QubesComponent {self.to_str()}>"

    def __eq__(self, other):
        return repr(self) == repr(other)

    def __hash__(self):
        return hash(self.name)

    def __str__(self):
        return self.to_str()
