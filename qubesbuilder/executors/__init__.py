# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
# Copyright (C) 2025 Rafał Wojdyła <omeg@invisiblethingslab.com>
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
import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple, Union

from qubesbuilder.common import sanitize_line, str_to_bool
from qubesbuilder.exc import QubesBuilderError


class ExecutorError(QubesBuilderError):
    """
    Base executor exception
    """

    pass


class Executor(ABC):
    """
    Base executor class
    """

    _builder_dir = Path("/builder")
    log = logging.getLogger("executor")

    def __init__(self, **kwargs):
        self._kwargs = kwargs

        clean: Union[str, bool] = self._kwargs.get("clean", True)
        self._clean = clean if isinstance(clean, bool) else str_to_bool(clean)

        clean_on_error: Union[str, bool] = self._kwargs.get(
            "clean_on_error", self._clean
        )
        self._clean_on_error = (
            clean_on_error
            if isinstance(clean_on_error, bool)
            else str_to_bool(clean_on_error)
        )

    def get_builder_dir(self):
        return self._builder_dir

    def get_build_dir(self):
        return self.get_builder_dir() / "build"

    def get_plugins_dir(self):
        return self.get_builder_dir() / "plugins"

    def get_sources_dir(self):
        return self.get_builder_dir() / "sources"

    def get_distfiles_dir(self):
        return self.get_builder_dir() / "distfiles"

    def get_repository_dir(self):
        return self.get_builder_dir() / "repository"

    def get_cache_dir(self):
        return self.get_builder_dir() / "cache"

    def get_dependencies_dir(self):
        return self.get_builder_dir() / "dependencies"

    @abstractmethod
    def copy_in(self, *args, **kwargs):
        pass

    @abstractmethod
    def copy_out(self, *args, **kwargs):
        pass

    @abstractmethod
    def run(self, *args, **kwargs):
        pass

    def get_user(self):
        raise NotImplementedError

    def get_group(self):
        raise NotImplementedError

    def get_placeholders(self):
        return {
            "@BUILDER_DIR@": self.get_builder_dir(),
            "@BUILD_DIR@": self.get_build_dir(),
            "@PLUGINS_DIR@": self.get_plugins_dir(),
            "@DISTFILES_DIR@": self.get_distfiles_dir(),
            "@DEPENDENCIES_DIR@": self.get_dependencies_dir(),
        }

    def replace_placeholders(self, s: str):
        for key, val in self.get_placeholders().items():
            s = s.replace(key, str(val))
        return s

    @staticmethod
    async def _read_stream(stream, callback, max_length=10000) -> bytes:
        remaining_line = b""
        buffer = b""

        while True:
            chunk = await stream.read(4096)
            if not chunk:
                if remaining_line and callback:
                    callback(sanitize_line(remaining_line).rstrip())
                break

            buffer += chunk
            lines = chunk.split(b"\n")

            lines[0] = remaining_line + lines[0]

            if callback:
                for line in lines[:-1]:
                    callback(sanitize_line(line).rstrip())

            remaining_line = lines[-1]
            if len(remaining_line) > max_length:
                line = remaining_line[:max_length]
                remaining_line = remaining_line[max_length:]
                if callback:
                    callback(sanitize_line(line).rstrip() + "\u2026")

        return buffer

    async def _stream_subprocess(
        self, cmd, stdout_cb, stderr_cb, stdin=b"", **kwargs
    ) -> Tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )

        if stdin:
            assert process.stdin is not None
            process.stdin.write(stdin)

        # we use this also for qrexec admin calls and they need stdin being closed
        assert process.stdin
        process.stdin.close()

        results = await asyncio.gather(
            self._read_stream(process.stdout, stdout_cb),
            self._read_stream(process.stderr, stderr_cb),
        )

        rc = await process.wait()
        return rc, results[0], results[1]

    def execute(self, cmd, collect=False, stdin=b"", echo=True, **kwargs):
        loop = asyncio.get_event_loop()

        rc, stdout, stderr = loop.run_until_complete(
            self._stream_subprocess(
                cmd=cmd,
                stdout_cb=self.log.debug if echo else None,
                stderr_cb=self.log.debug if echo else None,
                stdin=stdin,
                **kwargs,
            )
        )

        if collect:
            return rc, stdout, stderr

        return rc
