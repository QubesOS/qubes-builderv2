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
import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Union

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
    async def _read_stream(stream, cb, max_length=10000):
        remaining_line = b""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                if remaining_line:
                    cb(sanitize_line(remaining_line).rstrip())
                break
            lines = chunk.split(b"\n")

            lines[0] = remaining_line + lines[0]

            for line in lines[:-1]:
                cb(sanitize_line(line).rstrip())

            remaining_line = lines[-1]
            if len(remaining_line) > max_length:
                line = remaining_line[:max_length]
                remaining_line = remaining_line[max_length:]
                cb(sanitize_line(line).rstrip() + "\u2026")

    async def _stream_subprocess(self, cmd, stdout_cb, stderr_cb, **kwargs):
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )

        await asyncio.wait(
            [
                asyncio.create_task(
                    self._read_stream(process.stdout, stdout_cb)
                ),
                asyncio.create_task(
                    self._read_stream(process.stderr, stderr_cb)
                ),
            ]
        )
        return await process.wait()

    def execute(self, cmd, **kwargs):
        loop = asyncio.get_event_loop()
        rc = loop.run_until_complete(
            self._stream_subprocess(
                cmd, self.log.debug, self.log.debug, **kwargs
            )
        )
        return rc
