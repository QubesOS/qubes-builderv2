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
from qubesbuilder.executors import Executor, ExecutorError


def qrexec_call(
    executor: Executor,
    what: str,
    vm: str,
    service: str,
    args: Optional[List[str]] = None,
    options: Optional[List[str]] = None,
    stdin: bytes = b"",
    echo: bool = True,
    ignore_errors: bool = False,
) -> bytes:
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
    echo = echo and not admin
    rc, stdout, stderr = executor.execute(
        cmd, collect=True, stdin=stdin, echo=echo
    )

    if not ignore_errors and rc != 0:
        err = sanitize_line(stderr).rstrip() if stderr else ""
        msg = f"Failed to {what}: {err}qrexec call failed with code {rc}"
        raise ExecutorError(msg, name=vm)

    if admin:
        if not stdout.startswith(b"0\x00"):
            stdout = stdout[2:].replace(b"\x00", b"\n")

            msg = f"Failed to {what}: qrexec call failed: {stdout.decode('ascii', 'strict')}"
            if not ignore_errors:
                raise ExecutorError(msg)
            else:
                executor.log.debug(msg)
        stdout = stdout[2:]
    return stdout


def create_dispvm(executor: Executor, template: str) -> str:
    stdout = qrexec_call(
        executor=executor,
        what="create disposable qube",
        vm=template,
        service="admin.vm.CreateDisposable",
    )

    if not re.match(rb"\Adisp(0|[1-9][0-9]{0,8})\Z", stdout):
        raise ExecutorError("Failed to create disposable qube.")
    try:
        return stdout.decode("ascii", "strict")
    except UnicodeDecodeError as e:
        raise ExecutorError(f"Failed to obtain disposable qube name: {str(e)}")


def start_vm(executor: Executor, vm: str):
    qrexec_call(
        executor=executor,
        what="start vm",
        vm=vm,
        service="admin.vm.Start",
    )


def vm_state(executor: Executor, vm: str) -> str:
    stdout = qrexec_call(
        executor=executor,
        what="query vm state",
        vm=vm,
        service="admin.vm.CurrentState",
    )

    for state in stdout.decode("ascii", "strict").split():
        assert "=" in state
        kv = state.split("=")
        if kv[0] == "power_state":
            return kv[1]

    raise ExecutorError(
        f"Invalid response from admin.vm.CurrentState for '{vm}'"
    )


def kill_vm(executor: Executor, vm: str):
    qrexec_call(
        executor=executor,
        what="kill vm",
        vm=vm,
        service="admin.vm.Kill",
        ignore_errors=True,
    )


def remove_vm(executor: Executor, vm: str):
    qrexec_call(
        executor=executor,
        what="remove vm",
        vm=vm,
        service="admin.vm.Remove",
    )
