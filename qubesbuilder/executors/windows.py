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
from abc import ABC
from pathlib import Path, PurePath, PureWindowsPath
from time import sleep
from typing import List, Optional, Tuple

from qubesbuilder.common import sanitize_line
from qubesbuilder.executors import Executor, ExecutorError
from qubesbuilder.executors.qrexec import (
    create_dispvm,
    kill_vm as qkill_vm,
    qrexec_call,
    start_vm,
    vm_state,
)


class BaseWindowsExecutor(Executor, ABC):
    def __init__(
        self, ewdk: Optional[str] = None, user: str = "user", **kwargs
    ):
        super().__init__(**kwargs)
        self.ewdk_path = ewdk
        self.user = user

    def get_builder_dir(self):
        return PureWindowsPath("c:\\builder")

    def get_user(self):
        return self.user

    def get_threads(self) -> int:
        return self._kwargs.get("threads", 1)

    # starts dispvm from default template (not windows)
    def start_dispvm(self) -> str:
        name = create_dispvm(self, "dom0")
        self.log.debug(f"created dispvm {name}")
        start_vm(self, name)
        return name

    def kill_vm(self, vm: str):
        qkill_vm(self, vm)

    # Get loop device id for the EWDK iso if attached to the builder vm, otherwise None
    def _get_ewdk_loop(self) -> Optional[str]:
        if self.ewdk_path is None:
            return None

        rc, stdout, stderr = self.execute(
            ["losetup", "-j", self.ewdk_path], collect=True, echo=False
        )
        if rc != 0:
            raise ExecutorError(
                f"Failed to run losetup: {stderr.decode("ascii", "strict")}, error code {rc}"
            )

        result = stdout.decode("ascii", "strict")
        if "/dev/loop" in result:
            loop_dev = result.split(":", 1)[0]
            loop_id = loop_dev.removeprefix("/dev/")
            self.log.debug(f"ewdk loop id: {loop_id}")
            return loop_id
        else:
            return None

    def attach_ewdk(self, vm: str):
        assert self.ewdk_path
        if not Path(self.ewdk_path).is_file():
            raise ExecutorError(f"EWDK image not found at '{self.ewdk_path}'")

        loop_id = self._get_ewdk_loop()
        if not loop_id:
            # attach EWDK image
            proc = None
            self.log.debug(f"attaching EWDK from '{self.ewdk_path}'")
            rc, stdout, stderr = self.execute(
                ["sudo", "losetup", "-f", self.ewdk_path],
                collect=True,
                echo=False,
            )
            if rc != 0:
                raise ExecutorError(
                    f"Failed to run losetup: {stderr.decode("ascii", "strict")}, error code {rc}"
                )

            loop_id = self._get_ewdk_loop()
            if not loop_id:
                raise ExecutorError(f"Failed to attach EWDK ({self.ewdk_path})")

            self.log.debug(f"attached EWDK as '{loop_id}'")

        # wait for device to appear
        self_name = os.uname().nodename
        timeout = 10
        while timeout > 0:
            stdout = qrexec_call(
                executor=self,
                what="ewdk loop device query",
                vm=self_name,
                service=f"admin.vm.device.block.Available+{loop_id}",
            )

            if stdout.decode("ascii", "strict").startswith(loop_id):
                # loop device ready, attach to vm
                qrexec_call(
                    executor=self,
                    what="attach ewdk to worker vm",
                    vm=vm,
                    service=f"admin.vm.device.block.Attach+{self_name}+{loop_id}",
                    stdin=b"devtype=cdrom read-only=true persistent=true",
                )
                return

            timeout -= 1
            sleep(1)

        raise ExecutorError(
            f"Failed to attach EWDK ({self.ewdk_path}): "
            f"wait for loopback device timed out"
        )


class SSHWindowsExecutor(BaseWindowsExecutor):
    def __init__(
        self,
        ewdk: str,
        ssh_ip: str,
        ssh_key_path: str = "/home/user/.ssh/win-build.key",
        user: str = "user",
        ssh_vm: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(ewdk=ewdk, user=user, **kwargs)
        self.ip = ssh_ip
        self.key_path = ssh_key_path
        self.vm = ssh_vm

    def ssh_cmd(self, cmd: List[str]):
        target = f"{self.user}@{self.ip}"
        ret = self.execute(
            cmd=[
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
            ],
        )
        if ret != 0:
            raise ExecutorError(f"Failed to run SSH cmd {cmd}")

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
        target_path = src.replace("\\", "/")
        self.execute(
            [
                "scp",
                "-i",
                self.key_path,
                "-r",
                "-B",
                "-q",
                f"{target}:{target_path}",
                dst,
            ]
        )

    def start_worker(self):
        assert self.vm is not None
        # we need the vm in a stopped state to attach EWDK block device
        self.log.debug(f"starting worker vm: {self.vm}")

        try:
            self.attach_ewdk(self.vm)
        except ExecutorError as e:
            if "DeviceAlreadyAttached" in str(e):
                self.log.debug(f"EWDK already attached to vm '{self.vm}'")

        start_vm(self, self.vm)
        # ensure connectivity
        self.ssh_cmd(["exit 0"])

    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
    ):
        if self.vm is not None:
            self.start_worker()

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
