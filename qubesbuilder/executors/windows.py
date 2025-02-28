# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
# Copyright (C) 2024 Rafał Wojdyła <omeg@invisiblethingslab.com>
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

import subprocess
from abc import ABC
from pathlib import Path, PurePath, PureWindowsPath
from typing import List, Tuple

from qubesbuilder.common import sanitize_line
from qubesbuilder.executors import Executor, ExecutorError
from qubesbuilder.executors.qrexec import (
    create_dispvm,
    kill_vm as qkill_vm,
    qrexec_call,
    start_vm,
)


class BaseWindowsExecutor(Executor, ABC):
    def __init__(self, user: str = "user", **kwargs):
        super().__init__(**kwargs)
        self.user = user

    def get_builder_dir(self):
        return PureWindowsPath("c:\\builder")

    def get_user(self):
        return self.user

    def get_threads(self) -> int:
        return self._kwargs.get("threads", 1)

    # starts dispvm from default template (not windows)
    def start_dispvm(self) -> str:
        name = create_dispvm(self.log, "dom0")
        self.log.debug(f"created dispvm {name}")
        start_vm(self.log, name)
        return name

    def kill_vm(self, vm: str):
        qkill_vm(self.log, vm)

    def run_rpc_service(
        self,
        target: str,
        service: str,
        description: str,
        stdin: bytes = b"",
    ) -> bytes:
        out = qrexec_call(
            log=self.log,
            what=description,
            vm=target,
            service=service,
            capture_output=True,
            stdin=stdin,
        )
        assert out is not None
        return out


class SSHWindowsExecutor(BaseWindowsExecutor):
    def __init__(
        self,
        ssh_ip: str,
        ssh_key_path: str = "/home/user/.ssh/win-build.key",
        user: str = "user",
        **kwargs,
    ):
        super().__init__(user, **kwargs)
        self.ip = ssh_ip
        self.key_path = ssh_key_path

    def ssh_cmd(self, cmd: List[str]):
        target = f"{self.user}@{self.ip}"
        self.execute(
            [
                "ssh",
                "-i",
                self.key_path,
                "-o",
                "BatchMode yes",
                "-o",
                "StrictHostKeyChecking accept-new",
                "-o",
                "ConnectTimeout 60",
                target,
                "cmd",
                "/e",
                "/v:on",
                "/c",
                " & ".join(cmd),
                " & exit !errorlevel!",
            ]
        )

    def copy_in(self, source_path: Path, destination_dir: PurePath):
        src = str(source_path.expanduser().resolve())
        dst = str(destination_dir)
        self.log.debug(f"copy_in: {src} -> {dst}")

        self.ssh_cmd(
            [
                f'if not exist "{dst}" md "{dst}"',
            ]
        )

        target = f"{self.user}@{self.ip}"
        self.execute(
            [
                "scp",
                "-i",
                self.key_path,
                "-r",
                "-B",
                "-q",
                src,
                f"{target}:{dst}",
            ]
        )

    def copy_out(self, source_path: PurePath, destination_dir: Path):
        self.log.debug(f"copy_out: {source_path} -> {destination_dir}")
        src = str(source_path)
        dst = str(destination_dir.expanduser().resolve())

        target = f"{self.user}@{self.ip}"
        self.execute(
            [
                "scp",
                "-i",
                self.key_path,
                "-r",
                "-B",
                "-q",
                f"{target}:{src.replace('\\', '/')}",
                dst,
            ]
        )

    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
    ):
        # this executor doesn't use a dispvm, clear the build dir every time
        self.ssh_cmd(
            [
                f'if exist "{self.get_builder_dir()}" rmdir /s /q "{self.get_builder_dir()}"'
            ]
        )

        for src_in, dst_in in copy_in or []:
            self.copy_in(src_in, dst_in)

        self.ssh_cmd(cmd)

        for src_out, dst_out in copy_out or []:
            self.copy_out(src_out, dst_out)
