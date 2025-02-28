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
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePath
from shlex import quote
from time import sleep
from typing import List, Optional, Tuple, Union

from qubesbuilder.common import sanitize_line, PROJECT_PATH
from qubesbuilder.executors import Executor, ExecutorError
from qubesbuilder.executors.qrexec import (
    create_dispvm,
    kill_vm,
    qrexec_call,
    start_vm,
)
from qubesbuilder.executors.windows import BaseWindowsExecutor


# From https://github.com/QubesOS/qubes-core-admin-client/blob/main/qubesadmin/utils.py#L159-L173
def encode_for_vmexec(input_string):
    def encode(part):
        if part.group(0) == b"-":
            return b"--"
        return "-{:02X}".format(ord(part.group(0))).encode("ascii")

    part = re.sub(rb"[^a-zA-Z0-9_.]", encode, input_string.encode("utf-8"))

    return part.decode("ascii")


def quote_list(args: List[Union[str, Path]]) -> str:
    return " ".join(map(lambda x: quote(str(x)), args))


def quote_and_list(cmds: List[List[Union[str, Path]]]) -> str:
    return " && ".join(map(quote_list, cmds))


def build_run_cmd(vm_name: str, cmd: List[Union[str, Path]]) -> List[str]:
    return ["/usr/bin/qvm-run-vm", "--", vm_name, quote_list(cmd)]


def build_run_cmd_and_list(
    vm_name: str, cmds: List[List[Union[str, Path]]]
) -> List[str]:
    return ["/usr/bin/qvm-run-vm", "--", vm_name, quote_and_list(cmds)]


class QubesExecutor(Executor):
    def __init__(self, dispvm: str = "dom0", **kwargs):
        super().__init__(**kwargs)
        if dispvm == "@dispvm":
            self._dispvm_template = "dom0"
        else:
            self._dispvm_template = dispvm
        self.name = os.uname().nodename
        self.dispvm: Optional[str] = None  # actual dispvm name
        self.copy_in_service = "qubesbuilder.FileCopyIn"
        self.copy_out_service = "qubesbuilder.FileCopyOut"

    def get_user(self):
        return "user"

    def get_group(self):
        return "user"

    def copy_in(self, source_path: Path, destination_dir: PurePath):  # type: ignore
        assert self.dispvm
        src = source_path.expanduser().resolve()
        dst = destination_dir
        encoded_dst_path = encode_for_vmexec(str((dst / src.name).as_posix()))
        qrexec_call(
            log=self.log,
            what="copy-in",
            vm=self.dispvm,
            service=f"{self.copy_in_service}+{encoded_dst_path}",
            args=["/usr/lib/qubes/qfile-agent", str(src)],
        )

    def copy_out(
        self,
        source_path: PurePath,
        destination_dir: Path,
        dig_holes=False,
    ):  # type: ignore
        assert self.dispvm
        src = source_path
        dst = destination_dir.resolve()

        # Remove local file or directory if exists
        dst_path = dst / src.name
        if os.path.exists(dst_path):
            if dst_path.is_dir():
                shutil.rmtree(dst / src.name)
            else:
                os.remove(dst_path)

        dst.mkdir(parents=True, exist_ok=True)

        old_unpacker_path = "/usr/lib/qubes/qfile-unpacker"
        new_unpacker_path = "/usr/bin/qfile-unpacker"
        if os.path.exists(new_unpacker_path):
            unpacker_path = new_unpacker_path
        else:
            unpacker_path = old_unpacker_path
        encoded_src_path = encode_for_vmexec(str(src))
        qrexec_call(
            log=self.log,
            what="copy-out",
            vm=self.dispvm,
            service=f"{self.copy_out_service}+{encoded_src_path}",
            args=[
                unpacker_path,
                str(os.getuid()),
                str(dst),
            ],
        )

        if dig_holes and not dst_path.is_dir():
            try:
                self.log.debug(
                    "copy-out (detect zeroes and replace with holes)"
                )
                subprocess.run(
                    ["/usr/bin/fallocate", "-d", str(dst_path)], check=True
                )
            except subprocess.CalledProcessError as e:
                if e.stderr is not None:
                    content = sanitize_line(e.stderr.rstrip(b"\n")).rstrip()
                else:
                    content = str(e)
                msg = f"Failed to dig holes in copy-out: {content}"
                raise ExecutorError(msg, name=self.dispvm)

    def copy_rpc_services(self):
        assert self.dispvm
        qrexec_call(
            log=self.log,
            what="copy builder rpc services",
            vm=self.dispvm,
            service="qubes.Filecopy",
            args=[
                "/usr/lib/qubes/qfile-agent",
                str(PROJECT_PATH / "rpc" / self.copy_in_service),
                str(PROJECT_PATH / "rpc" / self.copy_out_service),
            ],
            options=["--filter-escape-chars-stderr"],
        )

    def cleanup(self):
        # Kill the DispVM to prevent hanging for while
        assert self.dispvm
        kill_vm(self.log, self.dispvm)


class LinuxQubesExecutor(QubesExecutor):
    def __init__(
        self, dispvm: str = "dom0", clean: Union[str, bool] = True, **kwargs
    ):
        super().__init__(dispvm=dispvm, clean=clean, **kwargs)

    def run(  # type: ignore
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
        files_inside_executor_with_placeholders: List[Path] = None,
        environment: dict = None,
        no_fail_copy_out_allowed_patterns=None,
        dig_holes: bool = False,
    ):
        try:
            self.dispvm = create_dispvm(self.log, self._dispvm_template)
            start_vm(self.log, self.dispvm)
            self.copy_rpc_services()

            assert self.dispvm
            prep_cmd = build_run_cmd_and_list(
                self.dispvm,
                [
                    [
                        "sudo",
                        "mkdir",
                        "-p",
                        "--",
                        str(self.get_builder_dir()),
                        str(self.get_builder_dir() / "build"),
                        str(self.get_builder_dir() / "plugins"),
                        str(self.get_builder_dir() / "distfiles"),
                        "/usr/local/etc/qubes-rpc",
                    ],
                    [
                        "sudo",
                        "mv",
                        "-f",
                        "--",
                        f"/home/{self.get_user()}/QubesIncoming/{self.name}/qubesbuilder.FileCopyIn",
                        f"/home/{self.get_user()}/QubesIncoming/{self.name}/qubesbuilder.FileCopyOut",
                        "/usr/local/etc/qubes-rpc/",
                    ],
                    [
                        "sudo",
                        "chmod",
                        "+x",
                        "--",
                        "/usr/local/etc/qubes-rpc/qubesbuilder.FileCopyIn",
                        "/usr/local/etc/qubes-rpc/qubesbuilder.FileCopyOut",
                    ],
                    [
                        "sudo",
                        "bash",
                        "-c",
                        "if [ -x /usr/sbin/restorecon ]; then restorecon -R /usr/local/etc/qubes-rpc/; fi;",
                    ],
                    [
                        "sudo",
                        "chown",
                        "-R",
                        "--",
                        f"{self.get_user()}:{self.get_group()}",
                        str(self.get_builder_dir()),
                    ],
                ],
            )
            subprocess.run(prep_cmd, stdin=subprocess.DEVNULL)

            # copy-in hook
            for src_in, dst_in in sorted(
                set(copy_in or []), key=lambda x: x[1]
            ):
                self.copy_in(source_path=src_in, destination_dir=dst_in)

            # replace placeholders
            if files_inside_executor_with_placeholders and isinstance(
                files_inside_executor_with_placeholders, list
            ):
                files = [
                    self.replace_placeholders(str(f))
                    for f in files_inside_executor_with_placeholders
                ]
                builder_dir = str(self.get_builder_dir())
                if "@" in builder_dir:
                    raise ExecutorError(
                        f"'@' not permitted in builder directory (got {builder_dir!r})"
                    )
                sed_rhs = (
                    builder_dir.replace("\\", "\\\\")
                    .replace("&", "\\&")
                    .replace("#", "\\#")
                    .replace("\n", "\\\n")
                )
                sed_cmd = build_run_cmd(
                    self.dispvm,
                    [
                        "sed",
                        "-i",
                        "--",
                        f"s#@BUILDER_DIR@#{sed_rhs}#g",
                    ]
                    + files,
                )
                subprocess.run(sed_cmd, stdin=subprocess.DEVNULL, check=True)

            bash_env = []
            if environment:
                for key, val in environment.items():
                    if "=" in str(key):
                        raise ExecutorError(
                            "Environment variable name cannot contain '='"
                        )
                    bash_env.append(f"{str(key)}={str(val)}")

            qvm_run_cmd = build_run_cmd(
                self.dispvm,
                [
                    "env",
                    "--",
                    *bash_env,
                    "bash",
                    "-c",
                    " && ".join(cmd),
                ],
            )

            self.log.info(f"Using executor qubes:{self.dispvm}.")
            self.log.debug(" ".join(qvm_run_cmd))

            # stream output for command
            rc = self.execute(qvm_run_cmd)
            if rc != 0:
                msg = f"Failed to run '{' '.join(qvm_run_cmd)}' (status={rc})."
                raise ExecutorError(msg, name=self.dispvm)

            # copy-out hook
            for src_out, dst_out in sorted(
                set(copy_out or []), key=lambda x: x[1]
            ):
                try:
                    self.copy_out(
                        source_path=src_out,
                        destination_dir=dst_out,
                        dig_holes=dig_holes,
                    )
                except ExecutorError as e:
                    # Ignore copy-out failure if requested
                    if isinstance(
                        no_fail_copy_out_allowed_patterns, list
                    ) and any(
                        [
                            p in src_out.name
                            for p in no_fail_copy_out_allowed_patterns
                        ]
                    ):
                        self.log.debug(
                            f"File not found inside container: {src_out}."
                        )
                        continue
                    raise e
        except (subprocess.CalledProcessError, ExecutorError) as e:
            if self.dispvm and self._clean_on_error:
                self.cleanup()
            raise e
        else:
            if self.dispvm and self._clean:
                self.cleanup()


class WindowsQubesExecutor(BaseWindowsExecutor, QubesExecutor):
    def __init__(
        self,
        ewdk: str,
        dispvm: str = "win-build",
        user: str = "user",
        clean: Union[str, bool] = True,
        **kwargs,
    ):
        super().__init__(dispvm=dispvm, user=user, clean=clean, **kwargs)
        self.ewdk_path = ewdk
        self.copy_in_service = "qubesbuilder.WinFileCopyIn"
        self.copy_out_service = "qubesbuilder.WinFileCopyOut"

    # Get loop device id for the EWDK iso if attached to the builder vm, otherwise None
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
                self.log.debug(f"ewdk loop id: {loop_id}")
                return loop_id
            else:
                return None

        except subprocess.CalledProcessError as e:
            raise ExecutorError(
                f"Failed to run losetup: {proc.stderr.decode()}"
            ) from e

    def attach_ewdk(self):
        if not Path(self.ewdk_path).is_file():
            raise ExecutorError(f"EWDK image not found at '{self.ewdk_path}'")

        loop_id = self._get_ewdk_loop()
        if not loop_id:
            # attach EWDK image
            proc = None
            try:
                self.log.debug(f"attaching EWDK from '{self.ewdk_path}'")
                proc = subprocess.run(
                    ["sudo", "losetup", "-f", self.ewdk_path],
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                raise ExecutorError(
                    f"Failed to run losetup: {proc.stderr.decode() if proc else e}"
                ) from e

            loop_id = self._get_ewdk_loop()
            if not loop_id:
                raise ExecutorError(f"Failed to attach EWDK ({self.ewdk_path})")

            self.log.debug(f"attached EWDK as '{loop_id}'")

        # wait for device to appear
        timeout = 10
        while timeout > 0:
            stdout = qrexec_call(
                log=self.log,
                what="ewdk loop device query",
                vm=self.name,
                service=f"admin.vm.device.block.Available+{loop_id}",
            )

            assert stdout is not None
            assert self.dispvm
            if stdout.decode("ascii", "strict").startswith(loop_id):
                # loop device ready, attach to vm
                qrexec_call(
                    log=self.log,
                    what="attach ewdk to dispvm",
                    vm=self.dispvm,
                    service=f"admin.vm.device.block.Attach+{self.name}+{loop_id}",
                    stdin=b"devtype=cdrom read-only=true persistent=true",
                )
                return

            timeout -= 1
            sleep(1)

        raise ExecutorError(
            f"Failed to attach EWDK ({self.ewdk_path}): "
            f"wait for loopback device timed out"
        )

    def start_worker(self):
        # we need the dispvm in a stopped state to attach EWDK block device
        self.dispvm = create_dispvm(self.log, self._dispvm_template)
        self.log.debug(f"dispvm: {self.dispvm}")
        self.attach_ewdk()
        start_vm(self.log, self.dispvm)

        # wait for startup
        for _ in range(10):
            try:
                qrexec_call(
                    log=self.log,
                    what="dispvm qrexec test",
                    vm=self.dispvm,
                    service="qubes.VMShell",
                    stdin=b"exit 0",
                )
                return  # all good
            except ExecutorError as e:
                self.log.debug(f"VMShell failed: {e}")
            sleep(5)
        raise ExecutorError(
            f"Failed to communicate with windows dispvm '{self.dispvm}'"
        )

    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
    ):
        try:
            # copy the rpc handlers
            self.start_worker()
            # TODO: don't require two scripts per service
            assert self.dispvm
            qrexec_call(
                log=self.log,
                what="copy RPC services to dispvm",
                vm=self.dispvm,
                service="qubes.Filecopy",
                args=[
                    "/usr/lib/qubes/qfile-agent",
                    str(PROJECT_PATH / "rpc" / "qubesbuilder.WinFileCopyIn"),
                    str(PROJECT_PATH / "rpc" / "qubesbuilder.WinFileCopyOut"),
                    str(PROJECT_PATH / "rpc" / "qubesbuilder-file-copy-in.ps1"),
                    str(
                        PROJECT_PATH / "rpc" / "qubesbuilder-file-copy-out.ps1"
                    ),
                ],
            )

            inc_dir = (
                f"c:\\users\\{self.user}\\Documents\\QubesIncoming\\{self.name}"
            )

            prep_cmd = [
                f'move /y "{inc_dir}\\qubesbuilder.WinFileCopyIn" "%QUBES_TOOLS%\\qubes-rpc\\"',
                f'move /y "{inc_dir}\\qubesbuilder.WinFileCopyOut" "%QUBES_TOOLS%\\qubes-rpc\\"',
                f'move /y "{inc_dir}\\qubesbuilder-file-copy-in.ps1" "%QUBES_TOOLS%\\qubes-rpc-services\\"',
                f'move /y "{inc_dir}\\qubesbuilder-file-copy-out.ps1" "%QUBES_TOOLS%\\qubes-rpc-services\\"',
            ]

            qrexec_call(
                log=self.log,
                what="prepare RPC services in dispvm",
                vm=self.dispvm,
                service="qubes.VMShell",
                stdin=(
                    " & ".join(prep_cmd) + " & exit !errorlevel!" + "\r\n"
                ).encode("utf-8"),
            )

            for src_in, dst_in in copy_in or []:
                self.copy_in(src_in, dst_in)

            bin_cmd = (
                " & ".join(cmd) + " & exit !errorlevel!" + "\r\n"
            ).encode("utf-8")
            self.log.debug(f"{bin_cmd=}")

            # TODO: this doesn't stream stdout, use execute()
            stdout = qrexec_call(
                log=self.log,
                what="run command in dispvm",
                vm=self.dispvm,
                service="qubes.VMShell",
                stdin=bin_cmd,
                capture_output=True,
            )
            assert stdout is not None
            self.log.debug(stdout.decode("utf-8"))

            for src_out, dst_out in copy_out or []:
                self.copy_out(src_out, dst_out)
        except ExecutorError as e:
            suffix = f" in qube {self.dispvm}" if self.dispvm else ""
            raise ExecutorError(
                f"Failed to run command{suffix}: {str(e)}"
            ) from e
        finally:
            if self.dispvm:
                kill_vm(self.log, self.dispvm)
