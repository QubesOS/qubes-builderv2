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

import os
import re
import subprocess
from pathlib import Path, PurePath, PureWindowsPath
from time import sleep
from typing import List, Optional, Tuple, Union

from qubesadmin import Qubes
from qubesadmin.devices import DeviceAssignment, UnknownDevice
from qubesadmin.exc import DeviceAlreadyAttached, QubesException
from qubesadmin.utils import encode_for_vmexec
from qubesadmin.vm import DispVM, QubesVM
from qubesbuilder.common import sanitize_line, PROJECT_PATH
from qubesbuilder.log import get_logger
from qubesbuilder.executors import Executor, ExecutorError


log = get_logger("WindowsExecutor")

ESCAPE_RE = re.compile(rb"--|-([A-F0-9]{2})")

def decode_part(part):
    if not re.match(r"^[a-zA-Z0-9._-]*$", part):
        raise DecodeError("illegal characters found")

    part = part.encode("ascii")

    # Check if no '-' remains outside of legal escape sequences.
    if b"-" in ESCAPE_RE.sub(b"", part):
        raise DecodeError("'-' can be used only in '-HH' or '--'")

    def convert(m):
        if m.group(0) == b"--":
            return b"-"
        num = int(m.group(1), 16)
        return bytes([num])

    return ESCAPE_RE.sub(convert, part).decode("utf-8")


class WindowsExecutor(Executor):
    def __init__(self, ewdk: str, vm: str = "win-build", user: str = "user", **kwargs):
        log.debug(f"Windows executor init: {ewdk=}, {vm=}, {user=}, params: {kwargs}")
        super().__init__(**kwargs)
        self.vm_name = vm
        self.user = user
        self.ewdk_path = ewdk
        self.app = Qubes()
        self.vm = QubesVM(self.app, self.vm_name)
        self.ssh_host = f"{self.user}@{self.vm.ip}"
        self.ssh_key = kwargs.get("ssh-key", "/home/user/.ssh/win-build.key")
        self.start_worker()
        self.use_qrexec = self.check_qrexec()
        log.debug(f"{self.use_qrexec=}")
        self.rpc_copied = False
        self.ensure_worker()


    def get_builder_dir(self):
        return PureWindowsPath("c:\\builder")


    def get_user(self):
        return self.user


    def get_threads(self) -> int:
        return self._kwargs.get("threads", 1)


    def run_local_cmd(self, cmd: List[str]):
        try:
            log.debug(f"cmd: '{' '.join(cmd)}'")
            result = subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            if e.stderr is not None:
                content = sanitize_line(e.stderr.rstrip(b"\n")).rstrip()
            else:
                content = str(e)
            msg = f"command '{cmd}' failed"
            raise ExecutorError(msg, name=self.vm_name) from e


    def ssh_cmd(self, cmd: List[str]):
        self.run_local_cmd([
            "ssh",
            "-i", self.ssh_key,
            "-o", "BatchMode yes",
            "-o", "StrictHostKeyChecking accept-new",
            "-o", "ConnectTimeout 60",
            self.ssh_host,
            "cmd", "/e", "/v:on", "/c",
            " & ".join(cmd),
            " & exit !errorlevel!",
        ])


    # Get loop device id for the EWDK iso if mounted, otherwise None
    def _get_ewdk_loop(self) -> Optional[str]:
        try:
            proc = subprocess.run(
                ["losetup", "-j", self.ewdk_path],
                check=True,
                capture_output=True,
            )
            stdout = proc.stdout.decode()
            if "/dev/loop" in stdout:
                loop_dev = stdout.split(":", 1)[0]
                loop_id = loop_dev.removeprefix("/dev/")
                log.debug(f"ewdk loop id: {loop_id}")
                return loop_id
            else:
                return None

        except subprocess.CalledProcessError as e:
            raise ExecutorError(f"Failed to run losetup: {proc.stderr.decode()}") from e

    def _get_ewdk_assignment(self) -> DeviceAssignment:
        if not Path(self.ewdk_path).is_file():
            raise ExecutorError(f"EWDK image not found at '{self.ewdk_path}'")

        loop_id = self._get_ewdk_loop()
        if not loop_id:
            # attach the image
            proc = None
            try:
                log.debug(f"attaching ewdk from '{self.ewdk_path}'")
                proc = subprocess.run(
                    ["sudo", "losetup", "-f", self.ewdk_path],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                raise ExecutorError(f"Failed to run losetup: {proc.stderr.decode() if proc else e}") from e

            loop_id = self._get_ewdk_loop()
            if not loop_id:
                raise ExecutorError(f"Failed to attach EWDK ({self.ewdk_path})")

            log.debug(f"attached ewdk as '{loop_id}'")

        # wait for device to appear
        self_vm = QubesVM(self.app, self.app.local_name)
        timeout = 10
        while isinstance(self_vm.devices['block'][loop_id], UnknownDevice):
            if timeout == 0:
                raise ExecutorError(f"Failed to attach EWDK ({self.ewdk_path}): "
                    f"wait for loopback device timed out")
            timeout -= 1
            sleep(1)

        return DeviceAssignment(
            backend_domain=self.app.local_name,
            ident=loop_id,
            devclass="block",
            options={
                "devtype": "cdrom",
                "read-only": True,
            },
            persistent=True)


    def create_dispvm(self) -> QubesVM:
        return DispVM.from_appvm(self.app, self._kwargs.get("dispvm"))


    def check_qrexec(self) -> bool:
        try:
            proc = self.vm.run_service("qubes.VMShell")
            proc.communicate(b"exit 0")
            return proc.returncode == 0
        except QubesException as e:
            log.debug(f"VMShell failed: {e}")
            return False


    def start_worker(self):
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


    def ensure_worker(self):
        try:
            if self.use_qrexec:
                assert self.check_qrexec()
            else:
                self.ssh_cmd(["exit 0"])
        except ExecutorError as e:
            msg = f"Worker VM {self.vm_name} failed to start or respond"
            raise ExecutorError(msg, name=self.vm_name) from e


    def copy_in(self, source_path: Path, destination_dir: PurePath):
        src = str(source_path.expanduser().resolve())
        dst = str(destination_dir)
        log.debug(f"copy_in: {src} -> {dst}")

        if self.use_qrexec:
            encoded_dst = encode_for_vmexec([dst])

            try:
                proc = self.vm.run_service(
                    f"qubesbuilder.WinFileCopyIn+{encoded_dst}",
                    localcmd=f"/usr/lib/qubes/qfile-agent {src}",
                )
                proc.communicate()
                if proc.returncode != 0:
                    raise QubesException(f"qubesbuilder.WinFileCopyIn returned with code {proc.returncode}")
            except QubesException as e:
                msg = f"Failed to run qubesbuilder.WinFileCopyIn service in qube '{self.vm_name}'"
                raise ExecutorError(msg, name=self.vm_name) from e

            return

        self.ssh_cmd([
            f"if not exist \"{dst}\" md \"{dst}\"",
        ])

        self.run_local_cmd([
            "scp",
            "-i", self.ssh_key,
            "-r", "-B", "-q",
            src,
            f"{self.ssh_host}:{dst}",
        ])


    def copy_out(self, source_path: PurePath, destination_dir: Path):
        log.debug(f"copy_out: {source_path} -> {destination_dir}")
        src = str(source_path)
        dst = str(destination_dir.expanduser().resolve())

        if self.use_qrexec:
            encoded_src = encode_for_vmexec([src])

            unpacker_path = "/usr/lib/qubes/qfile-unpacker"
            new_unpacker_path = "/usr/bin/qfile-unpacker"
            if os.path.exists(new_unpacker_path):
                unpacker_path = new_unpacker_path

            try:
                proc = self.vm.run_service(
                    f"qubesbuilder.WinFileCopyOut+{encoded_src}",
                    localcmd=f"{unpacker_path} {os.getuid()} {dst}",
                )
                proc.communicate()
                if proc.returncode != 0:
                    raise QubesException(f"qubesbuilder.WinFileCopyOut returned with code {proc.returncode}")
            except QubesException as e:
                msg = f"Failed to run qubesbuilder.WinFileCopyOut service in qube '{self.vm_name}'"
                raise ExecutorError(msg, name=self.vm_name) from e

            return

        self.run_local_cmd([
            "scp",
            "-i", self.ssh_key,
            "-r", "-B", "-q",
            f"{self.ssh_host}:{src.replace('\\', '/')}",
            dst,
        ])


    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
    ):
        if self.use_qrexec and not self.rpc_copied:
            # copy the rpc handlers
            files = [
                str(PROJECT_PATH / "rpc" / "qubesbuilder.WinFileCopyIn"),
                str(PROJECT_PATH / "rpc" / "qubesbuilder.WinFileCopyOut"),
                str(PROJECT_PATH / "rpc" / "qubesbuilder-file-copy-in.ps1"),
                str(PROJECT_PATH / "rpc" / "qubesbuilder-file-copy-out.ps1"),
            ]

            try:
                proc = self.vm.run_service(
                    "qubes.Filecopy",
                    localcmd=f"/usr/lib/qubes/qfile-agent {' '.join(files)}",
                )
                proc.communicate()
                if proc.returncode != 0:
                    raise QubesException(f"qubes.Filecopy returned with code {proc.returncode}")

                inc_dir = f"c:\\users\\{self.user}\\Documents\\QubesIncoming\\{os.uname().nodename}"

                prep_cmd = [
                    f"move /y \"{inc_dir}\\qubesbuilder.WinFileCopyIn\" \"%QUBES_TOOLS%\\qubes-rpc\\\"",
                    f"move /y \"{inc_dir}\\qubesbuilder.WinFileCopyOut\" \"%QUBES_TOOLS%\\qubes-rpc\\\"",
                    f"move /y \"{inc_dir}\\qubesbuilder-file-copy-in.ps1\" \"%QUBES_TOOLS%\\qubes-rpc-services\\\"",
                    f"move /y \"{inc_dir}\\qubesbuilder-file-copy-out.ps1\" \"%QUBES_TOOLS%\\qubes-rpc-services\\\"",
                ]

                proc = self.vm.run_service("qubes.VMShell")
                proc.communicate((" & ".join(prep_cmd) + " & exit !errorlevel!" + "\r\n").encode("utf-8"))
                if proc.returncode != 0:
                    raise QubesException(f"qubes.VMShell returned with code {proc.returncode}")
                self.rpc_copied = True
            except QubesException as e:
                msg = f"Failed to copy builder RPC services to qube '{self.vm_name}'"
                raise ExecutorError(msg, name=self.vm_name) from e

        for src_in, dst_in in copy_in or []:
            self.copy_in(src_in, dst_in)

        if self.use_qrexec:
            bin_cmd = (" & ".join(cmd) + " & exit !errorlevel!" + "\r\n").encode("utf-8")
            log.debug(f"{bin_cmd=}")
            proc = self.vm.run_service("qubes.VMShell")
            stdout, stderr = proc.communicate(bin_cmd)
            if proc.returncode != 0:
                raise QubesException(f"qubes.VMShell returned with code {proc.returncode}: {stderr.decode("utf-8")}")
            log.debug(stdout.decode("utf-8"))
        else:
            self.ssh_cmd(cmd)

        for src_out, dst_out in copy_out or []:
            self.copy_out(src_out, dst_out)
