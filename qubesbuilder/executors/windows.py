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

try:
    import qubesadmin
    import qubesadmin.exc

    try:
        import qubesadmin.device_protocol

        _QUBESADMIN_NEW_API = True
    except ImportError:
        import qubesadmin.devices

        _QUBESADMIN_NEW_API = False
except ImportError:
    qubesadmin = None
    _QUBESADMIN_NEW_API = False

from qubesbuilder.common import sha256sum
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

    @staticmethod
    def decode_win(data: bytes) -> str:
        return data.decode("utf-8", errors="replace")

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
                f"Failed to run losetup: {stderr.decode('ascii', 'strict')}, error code {rc}"
            )

        result = stdout.decode("ascii", "strict")
        if "/dev/loop" in result:
            loop_dev = result.split(":", 1)[0]
            loop_id = loop_dev.removeprefix("/dev/")
            self.log.debug(f"ewdk loop id: {loop_id}")
            return loop_id
        else:
            return None

    @staticmethod
    def _device_matches(dev, backend, loop_id: str) -> bool:
        dev_backend = getattr(dev, "backend_domain", None)
        # 4.3+ API uses port_id, 4.2 API uses ident
        dev_port = str(
            getattr(dev, "port_id", None) or getattr(dev, "ident", "") or ""
        )
        if dev_backend != backend:
            return False
        return dev_port == loop_id or dev_port.startswith(f"{loop_id}::")

    def attach_ewdk(self, vm: str, vm_running: bool = False):
        assert self.ewdk_path
        if qubesadmin is None:
            raise ExecutorError(
                "qubesadmin is required for EWDK block device attachment"
            )
        if not Path(self.ewdk_path).is_file():
            raise ExecutorError(f"EWDK image not found at '{self.ewdk_path}'")

        loop_id = self._get_ewdk_loop()
        if not loop_id:
            self.log.debug(f"attaching EWDK from '{self.ewdk_path}'")
            rc, stdout, stderr = self.execute(
                ["sudo", "losetup", "-f", self.ewdk_path],
                collect=True,
                echo=False,
            )
            if rc != 0:
                raise ExecutorError(
                    f"Failed to run losetup: {stderr.decode('ascii', 'strict')}, error code {rc}"
                )
            loop_id = self._get_ewdk_loop()
            if not loop_id:
                raise ExecutorError(f"Failed to attach EWDK ({self.ewdk_path})")
            self.log.debug(f"attached EWDK as '{loop_id}'")

        self.log.debug(
            f"vm '{vm}' is {'running' if vm_running else 'halted'}, choosing attach method"
        )

        app = qubesadmin.Qubes()
        self_name = os.uname().nodename
        backend = app.domains.get_blind(app.local_name)
        frontend = app.domains.get_blind(vm)

        # wait for loop device to appear then attach/assign via qubesadmin
        timeout = 10
        while timeout > 0:
            if _QUBESADMIN_NEW_API:
                exposed = backend.devices["block"].get_exposed_devices()
            else:
                exposed = backend.devices["block"].available()

            for dev in exposed:
                if self._device_matches(dev, backend, loop_id):
                    if _QUBESADMIN_NEW_API:
                        mode = "manual" if vm_running else "required"
                        assignment = (
                            qubesadmin.device_protocol.DeviceAssignment.new(
                                backend_domain=backend,
                                port_id=loop_id,
                                devclass="block",
                                frontend_domain=frontend,
                                mode=mode,
                                options={
                                    "devtype": "cdrom",
                                    "read-only": "yes",
                                },
                            )
                        )
                        self.log.debug(
                            f"EWDK block device: vm_running={vm_running} mode={mode!r} backend={self_name} port={loop_id}"
                        )
                    else:
                        # 4.2: persistent=True for pre-start required attach
                        assignment = qubesadmin.devices.DeviceAssignment(
                            backend_domain=backend,
                            ident=loop_id,
                            options={"devtype": "cdrom", "read-only": "yes"},
                            persistent=not vm_running,
                            frontend_domain=frontend,
                            devclass="block",
                        )
                        self.log.debug(
                            f"EWDK block device: vm_running={vm_running} persistent={not vm_running} backend={self_name} port={loop_id}"
                        )
                    try:
                        if _QUBESADMIN_NEW_API:
                            if vm_running:
                                self.log.debug(f"attaching EWDK to '{vm}'")
                                frontend.devices["block"].attach(assignment)
                            else:
                                self.log.debug(f"assigning EWDK to '{vm}'")
                                frontend.devices["block"].assign(assignment)
                        else:
                            # 4.2: attach() handles both cases via persistent flag
                            self.log.debug(
                                f"{"attaching" if vm_running else "assigning"} EWDK to '{vm}'"
                            )
                            frontend.devices["block"].attach(assignment)
                    except (
                        qubesadmin.exc.DeviceAlreadyAttached,
                        qubesadmin.exc.QubesException,
                    ) as e:
                        msg = str(e)
                        if (
                            "already attached" in msg
                            or "already assigned" in msg
                        ):
                            self.log.debug(
                                f"EWDK already present on '{vm}', treating as success: {e}"
                            )
                            return
                        raise
                    self.log.debug(f"EWDK successfully attached to '{vm}'")
                    return

            timeout -= 1
            sleep(1)

        raise ExecutorError(
            f"Failed to attach EWDK ({self.ewdk_path}): wait for loopback device timed out"
        )


class SSHWindowsExecutor(BaseWindowsExecutor):
    def __init__(
        self,
        ssh_ip: str,
        ewdk: Optional[str] = None,
        ewdk_skip_checksum: bool = False,
        ewdk_mode: str = "attach",
        ssh_key_path: str = "/home/user/.ssh/win-build.key",
        user: str = "user",
        ssh_vm: Optional[str] = None,
        **kwargs,
    ):
        super().__init__(ewdk=ewdk, user=user, **kwargs)
        self.ip = ssh_ip
        self.key_path = ssh_key_path
        self.vm = ssh_vm
        self.ewdk_skip_checksum = ewdk_skip_checksum
        if ewdk_mode not in ("attach", "copy"):
            raise ExecutorError(
                f"Invalid ewdk-mode '{ewdk_mode}': must be 'attach' or 'copy'"
            )
        self.ewdk_mode = ewdk_mode
        if ewdk_mode == "attach" and ssh_vm is None and ewdk is not None:
            raise ExecutorError(
                "ewdk-mode 'attach' requires ssh-vm to be set: block-device attachment needs a qubes name"
            )

    def _ssh_base_cmd(self) -> List[str]:
        return [
            "ssh",
            "-i",
            self.key_path,
            "-o",
            "BatchMode yes",
            "-o",
            "StrictHostKeyChecking accept-new",
            "-o",
            "ConnectTimeout 60",
            f"{self.user}@{self.ip}",
        ]

    def ssh_cmd(self, cmd: List[str]) -> str:
        ret, stdout, stderr = self.execute(
            cmd=[
                *self._ssh_base_cmd(),
                "cmd",
                "/e",
                "/v:on",
                "/c",
                " & ".join(cmd),
                " & exit !errorlevel!",
            ],
            collect=True,
        )
        if ret != 0:
            raise ExecutorError(
                f"Failed to run SSH cmd {cmd}: {self.decode_win(stderr)}"
            )
        return self.decode_win(stdout)

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

    def _remote_file_exists(self, path: str) -> bool:
        """
        Check if a file exists on the remote Windows host.
        """
        try:
            out = self._run_powershell(f"Test-Path '{path}'")
            exists = out.strip().lower() == "true"
            self.log.debug(
                f"remote file '{path}': {'found' if exists else 'not found'}"
            )
            return exists
        except ExecutorError:
            self.log.debug(
                f"remote file '{path}': check failed, assuming not found"
            )
            return False

    def _remote_sha256(self, path: str) -> Optional[str]:
        """
        Return the lowercase SHA256 hex digest of a file on the remote host, or None if unavailable.
        """
        try:
            out = self._run_powershell(
                f"(Get-FileHash '{path}' -Algorithm SHA256).Hash"
            )
            return out.strip().lower()
        except ExecutorError:
            return None

    def _scp_file(self, local_path: Path, remote_path: str):
        """
        SCP a single file to the remote host at the exact path specified.
        """
        remote_path_fwd = remote_path.replace("\\", "/")
        self.execute(
            [
                "scp",
                "-i",
                self.key_path,
                "-B",
                "-q",
                str(local_path),
                f"{self.user}@{self.ip}:{remote_path_fwd}",
            ]
        )

    def _run_powershell(self, ps_script: str):
        """
        Run a PowerShell script on the remote host.
        """
        ret, stdout, stderr = self.execute(
            cmd=[
                *self._ssh_base_cmd(),
                "powershell",
                "-NonInteractive",
                "-Command",
                ps_script,
            ],
            collect=True,
            echo=False,
        )
        if ret != 0:
            raise ExecutorError(
                f"Failed to run PowerShell script: {self.decode_win(stderr)}"
            )
        return self.decode_win(stdout)

    def setup_remote(self):
        """
        Transfer and set up the EWDK ISO on the remote host.
        """
        if not self.ewdk_path:
            return

        ewdk_iso = Path(self.ewdk_path)
        if not ewdk_iso.is_file():
            raise ExecutorError(f"EWDK image not found at '{self.ewdk_path}'")

        remote_ewdk = f"c:\\Users\\{self.user}\\ewdk.iso"

        if self.ewdk_skip_checksum:
            if self._remote_file_exists(remote_ewdk):
                self.log.debug(
                    "EWDK ISO already present on remote host (checksum skipped)"
                )
            else:
                self.log.debug(
                    "EWDK ISO not found on remote host, transferring"
                )
                self._scp_file(ewdk_iso, remote_ewdk)
        else:
            local_digest = sha256sum(ewdk_iso)
            remote_digest = self._remote_sha256(remote_ewdk)

            if local_digest == remote_digest:
                self.log.debug(
                    "EWDK ISO already present on remote host with correct checksum"
                )
            else:
                if remote_digest is None:
                    self.log.debug(
                        "EWDK ISO not found on remote host, transferring"
                    )
                else:
                    self.log.debug(
                        f"EWDK ISO checksum mismatch (local={local_digest},remote={remote_digest}), re-transferring"
                    )
                self._scp_file(ewdk_iso, remote_ewdk)

        # Mount the ISO if not already mounted (idempotent)
        self.log.debug("Ensuring EWDK ISO is mounted on remote host")
        self._run_powershell(
            f"if (-not (Get-DiskImage -ImagePath '{remote_ewdk}').Attached) {{ Mount-DiskImage -ImagePath '{remote_ewdk}' }}"
        )

    def start_worker(self):
        assert self.vm is not None
        self.log.debug(f"starting worker vm: {self.vm}")

        state = vm_state(self, self.vm)
        self.log.debug(f"vm '{self.vm}' state: {state}")
        vm_running = state != "Halted"

        if self.ewdk_path and self.ewdk_mode == "attach" and not vm_running:
            # assign before start so the device is available at boot
            self.attach_ewdk(self.vm, vm_running=False)

        if not vm_running:
            start_vm(self, self.vm)

        # ensure connectivity
        self.ssh_cmd(["exit 0"])

        if self.ewdk_path and self.ewdk_mode == "attach" and vm_running:
            self.attach_ewdk(self.vm, vm_running=True)
        elif self.ewdk_path and self.ewdk_mode == "copy":
            self.setup_remote()

    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
    ) -> str:
        if self.vm is not None:
            self.start_worker()
        else:
            self.setup_remote()

        # this executor doesn't use a dispvm, clear the build dir every time
        self.ssh_cmd(
            [
                f'if exist "{self.get_builder_dir()}" rmdir /s /q "{self.get_builder_dir()}"'
            ]
        )

        for src_in, dst_in in copy_in or []:
            self.copy_in(src_in, dst_in)

        stdout = self.ssh_cmd(cmd)

        for src_out, dst_out in copy_out or []:
            self.copy_out(src_out, dst_out)

        return stdout
