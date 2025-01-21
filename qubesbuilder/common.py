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
import tempfile
import subprocess

from enum import Enum
from pathlib import Path
from string import digits, ascii_letters
from typing import List

PROJECT_PATH = Path(__file__).resolve().parents[1]

STAGES = [
    "fetch",
    "prep",
    "build",
    "post",
    "verify",
    "sign",
    "publish",
    "upload",
]
STAGES_ALIAS = {
    "f": "fetch",
    "b": "build",
    "po": "post",
    "v": "verify",
    "s": "sign",
    "pu": "publish",
    "u": "upload",
}
FORBIDDEN_PATTERNS = [".."]


class VerificationMode(Enum):
    SignedTag = "signed-tag"
    SignedCommit = "less-secure-signed-commits-sufficient"
    Insecure = "insecure-skip-checking"


for s in STAGES:
    FORBIDDEN_PATTERNS += [f".{s}.yml", f".{s}.yaml"]


def is_filename_valid(
    filename: str, allowed_ext: List[str] = None, forbidden_filename: str = None
) -> bool:
    if filename == "" or filename[0] in ("-", "."):
        return False
    if forbidden_filename and filename == forbidden_filename:
        return False
    if allowed_ext:
        p = Path(filename)
        if p.suffix not in allowed_ext:
            return False
    authorized_chars = digits + ascii_letters + "-_.+"
    for c in filename:
        if c not in authorized_chars:
            return False
    return True


def get_archive_name(file: dict):
    if "url" in file:
        fn = os.path.basename(file["url"])
        if file.get("uncompress", False):
            return Path(fn).with_suffix("").name
        return fn
    if "git-basename" in file:
        suffix = "tar" if file.get("uncompress", False) else "tar.gz"
        return f"{file['git-basename']}.{suffix}"
    else:
        archive_base = os.path.basename(file["git-url"]).partition(".git")[0]
        suffix = "tar" if file.get("uncompress", False) else "tar.gz"
        if "tag" in file:
            assert "/" not in file["tag"]
            return f"{archive_base}-{file['tag']}.{suffix}"
        if "commit-id" in file:
            return f"{archive_base}-{file['commit-id']}.{suffix}"
    return None


# Originally from QubesOS/qubes-builder/rpc-services/qubesbuilder.BuildLog
def sanitize_line(untrusted_line: bytes):
    line = bytearray(untrusted_line)
    for i, c in enumerate(line):
        if 0x20 <= c <= 0x7E:
            pass
        else:
            line[i] = 0x2E
    return bytearray(line).decode("ascii")


def str_to_bool(input_str: str) -> bool:
    input_str = input_str.lower()
    if input_str in ("true", "1"):
        return True
    else:
        return False


def deep_check(data):
    if isinstance(data, dict):
        for k, v in data.items():
            deep_check(k)
            deep_check(v)
    elif isinstance(data, list):
        for l in data:
            deep_check(l)
    elif isinstance(data, str):
        for p in FORBIDDEN_PATTERNS:
            if p in data:
                raise ValueError(f"Forbidden pattern '{p}' found in '{data}'.")
    elif isinstance(data, int):
        pass
    else:
        raise ValueError(f"Unexpected data type {type(data)} found")


def sed(pattern, replace, source, destination=None):
    """Reads a source file and writes the destination file.

    Args:
        pattern     (str): pattern to match (can be re.pattern)
        replace     (str): replacement string
        source      (str): input filename
        destination (str): destination filename (if not given, source will be overwritten)
    """

    with open(source, "r") as fd:
        data = fd.read()

    p = re.compile(pattern)
    sed_data = p.sub(replace, data)

    if destination:
        with open(destination, "w") as fd:
            fd.write(sed_data)
    else:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as fd:  # type: ignore
            fd.write(sed_data)
            fd.flush()
        shutil.move(fd.name, source)


def extract_lines_before(
    file_path, search_string, num_lines_before=10, max_split=4
):
    if file_path and file_path.exists():
        try:
            # Run grep to find the line number with the search string
            grep_result = subprocess.run(
                ["grep", "-n", search_string, file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Check if grep found the string
            if grep_result.returncode == 0:
                # Extract the line number from grep's output
                line_number = int(grep_result.stdout.split(":")[0])

                # Calculate the starting line (10 lines before the found line)
                start_line = max(1, line_number - num_lines_before)

                # Use sed to extract the range of lines (from start_line to line_number - 1)
                sed_result = subprocess.run(
                    ["sed", "-n", f"{start_line},{line_number}p", file_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                # Return the lines extracted by sed
                return [
                    line.split(" ", max_split)[-1]
                    for line in sed_result.stdout.splitlines()
                ], start_line
        except Exception:
            pass
    return None, None
