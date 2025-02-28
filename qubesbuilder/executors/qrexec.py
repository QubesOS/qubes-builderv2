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
import re
import subprocess
from typing import List, Optional

from qubesbuilder.common import sanitize_line
from qubesbuilder.executors import ExecutorError


def qrexec_call(
    log,
    what: str,
    vm: str,
    service: str,
    args: Optional[List[str]] = None,
    capture_output: bool = False,
    options: Optional[List[str]] = None,
    stdin: bytes = b"",
    ignore_errors: bool = False,
) -> Optional[bytes]:
    cmd = [
        "/usr/lib/qubes/qrexec-client-vm",
    ]

    if options:
        cmd += options

    cmd += [
        "--",
        vm,
        service,
    ]

    if args:
        cmd += args

    admin = service.startswith("admin.")
    capture_output = capture_output or admin
    try:
        log.debug(f"qrexec call ({what}): {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            check=not ignore_errors,
            capture_output=capture_output,
            input=stdin,
        )
    except subprocess.CalledProcessError as e:
        if e.stderr is not None:
            content = sanitize_line(e.stderr.rstrip(b"\n")).rstrip()
        else:
            content = str(e)
        msg = f"Failed to {what}: {content}"
        raise ExecutorError(msg, name=vm)

    if capture_output:
        stdout = proc.stdout
        if admin:
            if not stdout.startswith(b"0\x00"):
                stdout = stdout[2:].replace(b"\x00", b"\n")

                if not ignore_errors:
                    raise ExecutorError(
                        f"Failed to {what}: qrexec call failed: {stdout.decode("ascii", "strict")}"
                    )
                else:
                    log.debug(
                        f"Failed to {what}: qrexec call failed: {stdout.decode("ascii", "strict")}"
                    )
            stdout = stdout[2:]
        return stdout
    return None


def create_dispvm(log, template: str) -> str:
    stdout = qrexec_call(
        log=log,
        what="create disposable qube",
        vm=template,
        service="admin.vm.CreateDisposable",
    )

    assert stdout
    if not re.match(rb"\Adisp(0|[1-9][0-9]{0,8})\Z", stdout):
        raise ExecutorError("Failed to create disposable qube.")
    try:
        return stdout.decode("ascii", "strict")
    except UnicodeDecodeError as e:
        raise ExecutorError(f"Failed to obtain disposable qube name: {str(e)}")


def start_vm(log, vm: str):
    qrexec_call(
        log=log,
        what="start vm",
        vm=vm,
        service="admin.vm.Start",
    )


def kill_vm(log, vm: str):
    qrexec_call(
        log=log,
        what="kill vm",
        vm=vm,
        service="admin.vm.Kill",
        ignore_errors=True,
    )

    qrexec_call(
        log=log,
        what="remove vm",
        vm=vm,
        service="admin.vm.Remove",
        ignore_errors=True,
    )
