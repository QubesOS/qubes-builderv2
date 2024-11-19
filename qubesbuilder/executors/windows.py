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
from pathlib import Path, PurePath, PureWindowsPath
from typing import List, Tuple, Union

from qubesadmin import Qubes
from qubesadmin.devices import DeviceAssignment
from qubesadmin.exc import DeviceAlreadyAttached
from qubesadmin.vm import QubesVM
from qubesbuilder.common import sanitize_line
from qubesbuilder.log import get_logger
from qubesbuilder.executors import Executor, ExecutorError


log = get_logger("WindowsExecutor")


class WindowsExecutor(Executor):
    def __init__(self, vm: str = "win-build", user: str = "user", **kwargs):
        log.debug(f"Windows executor init: {vm=}, {user=}, params: {kwargs}")
        super().__init__(**kwargs)
        self.vm_name = vm
        self.user = user
        self.app = Qubes()
        self.vm = QubesVM(self.app, self.vm_name)
        self.ssh_host = f"{self.user}@{self.vm.ip}"
        self.ssh_key = kwargs.get("ssh-key", "/home/user/.ssh/win-build.key")


    def get_builder_dir(self):
        return PureWindowsPath("c:\\builder")


    def get_user(self):
        return self.user


    def get_threads(self) -> int:
        return self._kwargs.get("threads", 1)


    def run_cmd(self, cmd: List[str]):
        try:
            result = subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            if e.stderr is not None:
                content = sanitize_line(e.stderr.rstrip(b"\n")).rstrip()
            else:
                content = str(e)
            msg = f"command '{cmd}' failed"
            raise ExecutorError(msg, name=self.vm_name) from e


    def ssh_cmd(self, cmd: List[str]):
        self.run_cmd([
            "ssh",
            "-i", self.ssh_key,
            "-o", "BatchMode yes",
            "-o", "StrictHostKeyChecking accept-new",
            "-o", "ConnectTimeout 60",
            self.ssh_host,
        ] + cmd)


    def _get_ewdk_assignment(self) -> DeviceAssignment:
        # TODO: perform losetup if not mounted etc
        ident = self._kwargs["ewdk"].split(':')
        backend_domain = ident[0]
        backend_loop = ident[1]
        return DeviceAssignment(
            backend_domain = ident[0],
            ident = ident[1],
            devclass = "block",
            options = {
                "devtype": "cdrom",
                "read-only": True,
            },
            persistent = True)


    def ensure_vm(self):
        if not self.vm.is_running():
            ewdk_assignment = self._get_ewdk_assignment()

            try:
                self.vm.devices["block"].attach(ewdk_assignment)
            except DeviceAlreadyAttached:
                pass
            except Exception as e:
                msg = f"Failed to assign EWDK iso image to worker vm {self.vm_name}"
                raise ExecutorError(msg, name=self.vm_name) from e

            self.vm.start()

        try:
            self.ssh_cmd(["exit"])
        except ExecutorError as e:
            msg = f"Worker VM {self.vm_name} failed to start or respond"
            raise ExecutorError(msg, name=self.vm_name) from e


    def copy_in(self, source_path: Path, destination_dir: PurePath):
        log.debug(f"copy_in: {source_path} -> {destination_dir}")
        src = str(source_path.expanduser().resolve())
        dst = str(destination_dir)

        self.ssh_cmd([
            "cmd",
            "/e", # enable extensions to create all intermediate dirs
            "/c",
            "if", "not", "exist", dst,
            "md",
            dst,
        ])

        self.run_cmd([
            "scp",
            "-i", self.ssh_key,
            "-r", "-B", "-q",
            src,
            f"{self.ssh_host}:{dst}",
        ])


    def copy_out(self, source_path: PurePath, destination_dir: Path):
        log.debug(f"copy_out: {source_path} -> {destination_dir}")
        src = str(source_path).replace('\\', '/')
        dst = str(destination_dir.expanduser().resolve())
        self.run_cmd([
            "scp",
            "-i", self.ssh_key,
            "-r", "-B", "-q",
            f"{self.ssh_host}:{src}",
            dst,
        ])


    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
    ):
        self.ensure_vm()
        for src_in, dst_in in copy_in or []:
            self.copy_in(src_in, dst_in)

        self.ssh_cmd(cmd)

        for src_out, dst_out in copy_out or []:
            self.copy_out(src_out, dst_out)
