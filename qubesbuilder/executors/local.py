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
import getpass
import grp
import os
import pwd
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import List, Tuple, Union

from qubesbuilder.common import str_to_bool
from qubesbuilder.executors import Executor, ExecutorError


class LocalExecutor(Executor):
    """
    Local executor
    """

    def __init__(
        self,
        directory: Path = Path("/tmp"),
        **kwargs,
    ):
        super().__init__(**kwargs)

        random_path = str(id(self)) + str(uuid.uuid4())[0:8]
        self._directory = directory
        self._temporary_dir = (
            Path(self._directory).expanduser().resolve() / random_path
        )
        self._builder_dir = self._temporary_dir / "builder"
        self._builder_dir_exists = False

    def get_directory(self):
        return self._directory

    def get_user(self):
        return self._kwargs.get("user", getpass.getuser())

    def get_group(self):
        gid = pwd.getpwnam(self.get_user()).pw_gid
        group = grp.getgrgid(gid).gr_name
        return self._kwargs.get("group", group)

    def copy_in(self, source_path: Path, destination_dir: Path, action="copy-in"):  # type: ignore
        src = source_path.resolve()
        dst = destination_dir.resolve()
        try:
            if src.is_dir():
                dst = dst / src.name
                if dst.exists():
                    shutil.rmtree(str(dst))
                shutil.copytree(str(src), str(dst), symlinks=True)
            else:
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
        except (shutil.Error, FileExistsError, FileNotFoundError) as e:
            msg = f"Failed to {action}: {e!s}"
            raise ExecutorError(msg) from e

    def copy_out(self, source_path: Path, destination_dir: Path):  # type: ignore
        self.copy_in(source_path, destination_dir, action="copy-out")

    def cleanup(self):
        try:
            shutil.rmtree(self._temporary_dir)
        except PermissionError:
            # retry with sudo
            try:
                subprocess.run(
                    [
                        "sudo",
                        "--non-interactive",
                        "rm",
                        "-rf",
                        "--",
                        self._temporary_dir,
                    ],
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                raise ExecutorError(
                    f"Failed to clean executor temporary directory: {str(e)}"
                )
        except OSError as e:
            raise ExecutorError(
                f"Failed to clean executor temporary directory: {str(e)}"
            )

    def run(  # type: ignore
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, Path]] = None,
        copy_out: List[Tuple[Path, Path]] = None,
        files_inside_executor_with_placeholders: List[Path] = None,
        environment=None,
        no_fail_copy_out_allowed_patterns=None,
        **kwargs,
    ):
        # Create temporary builder directory. In an unlikely case of conflict,
        # run will abort instead of using unsafe directory.
        try:
            self._builder_dir.mkdir(
                parents=True, exist_ok=self._builder_dir_exists
            )
            self._builder_dir_exists = True
        except (FileNotFoundError, OSError) as e:
            raise ExecutorError(
                f"Failed to create temporary builder directory: {str(e)}"
            )

        try:
            # copy-in hook
            for src, dst in sorted(set(copy_in or []), key=lambda x: x[1]):
                self.copy_in(
                    source_path=src,
                    destination_dir=dst,
                )

            # replace placeholders
            sed_cmd = ""
            if files_inside_executor_with_placeholders and isinstance(
                files_inside_executor_with_placeholders, list
            ):
                files = [
                    self.replace_placeholders(str(f))
                    for f in files_inside_executor_with_placeholders
                ]
                sed_cmd = f"sed -i 's#@BUILDER_DIR@#{self.get_builder_dir()}#g' {' '.join(files)};"

            final_cmd = [
                "bash",
                "-c",
                sed_cmd + "&&".join(cmd),
            ]

            self.log.debug(
                f"Using executor local:{self._builder_dir} to run '{final_cmd}'."
            )

            # add requested env to existing env, instead of completely replacing it
            if environment is not None:
                environment_new = os.environ.copy()
                environment_new.update(environment)
                environment = environment_new

            rc = self.execute(final_cmd, env=environment)
            if rc != 0:
                msg = f"Failed to run '{final_cmd}' (status={rc})."
                raise ExecutorError(msg)

            # copy-out hook
            for src, dst in sorted(set(copy_out or []), key=lambda x: x[1]):
                try:
                    self.copy_out(source_path=src, destination_dir=dst)
                except ExecutorError as e:
                    # Ignore copy-out failure if requested
                    if isinstance(
                        no_fail_copy_out_allowed_patterns, list
                    ) and any(
                        [
                            p in src.name
                            for p in no_fail_copy_out_allowed_patterns
                        ]
                    ):
                        self.log.debug(
                            f"File not found inside container: {src}."
                        )
                        continue
                    raise e
        except ExecutorError as e:
            if self._temporary_dir.exists() and self._clean_on_error:
                self.cleanup()
            raise e
        else:
            if self._temporary_dir.exists() and self._clean:
                self.cleanup()
