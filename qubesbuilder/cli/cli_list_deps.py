# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2026 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern, Sequence, Set, Tuple

import click
import yaml

from qubesbuilder.cli.cli_base import aliased_group, ContextObj
from qubesbuilder.cli.cli_package import _component_stage
from qubesbuilder.log import QubesBuilderLogger


DEFAULT_EXCLUDES: Tuple[str, ...] = ("^qubes-",)


class SingleQuoted(str):
    """str subclass that PyYAML emits with single quotes."""


def _emit_single_quoted(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'")


yaml.SafeDumper.add_representer(SingleQuoted, _emit_single_quoted)


_NAME_RE = re.compile(r"^[A-Za-z0-9._+\/\-:~]+$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._+\-~:]+$")
_OPS = frozenset({">=", "<=", ">", "<", "="})
_MAX_DEP_LEN = 100  # e.g. "python3-some-long-package-name >= 1.2.3" is 39


def is_safe_dep(line: str) -> bool:
    """Accept 'NAME' or 'NAME OP VERSION'; reject everything else."""
    if not line or len(line) > _MAX_DEP_LEN:
        return False
    parts = line.split()
    if len(parts) == 1:
        name = parts[0]
        if ".." in name.split("/"):
            return False
        return bool(_NAME_RE.match(name))
    if len(parts) == 3:
        name, op, version = parts
        if ".." in name.split("/"):
            return False
        return (
            bool(_NAME_RE.match(name))
            and op in _OPS
            and bool(_VERSION_RE.match(version))
        )
    return False


def _compile_excludes(
    patterns: Optional[Iterable[str]],
) -> List[Pattern[str]]:
    return [re.compile(p) for p in (patterns or ())]


def _normalize_for_cache(
    deps: List[str],
    excludes: Optional[Sequence[str]] = None,
) -> List[str]:
    op_re = re.compile(r"\s*(<=|>=|<|>|=)\s*")
    compiled = _compile_excludes(excludes)
    by_head: Dict[str, Set[str]] = {}
    for raw in deps:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("("):
            continue
        if not is_safe_dep(line):
            QubesBuilderLogger.warning(
                f"list-deps: dropping dep with unexpected characters: {line!r}"
            )
            continue
        head = op_re.split(line, maxsplit=1)[0].strip()
        if not head:
            continue
        if any(p.search(head) for p in compiled):
            continue
        by_head.setdefault(head, set()).add(line)

    out: Set[str] = set()
    for head, forms in by_head.items():
        constrained = {f for f in forms if f != head}
        if constrained:
            out.update(constrained)
        else:
            out.add(head)
    return sorted(out)


def _resolve_excludes(
    obj: ContextObj, cli_excludes: Sequence[str]
) -> List[str]:
    conf = obj.config.get("list-deps", {}) or {}
    if "exclude" in conf:
        base = list(conf.get("exclude") or [])
    else:
        base = list(DEFAULT_EXCLUDES)
    base.extend(cli_excludes)
    return base


def _run_stage(obj: ContextObj):
    if obj.config.get("skip-git-fetch", None) is None:
        obj.config.set("skip-git-fetch", False)
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["list-deps"],
    )


def _collect_cache_section(
    obj: ContextObj, excludes: Optional[Sequence[str]] = None
) -> dict:
    by_dist: Dict[str, Set[str]] = {}
    for component in obj.components:
        for dist in obj.distributions:
            stage_dir = (
                obj.config.artifacts_dir
                / "components"
                / component.name
                / component.get_version_release()
                / dist.distribution
                / "list-deps"
            )
            if not stage_dir.exists():
                continue
            for info_file in sorted(stage_dir.glob("*.list-deps.yml")):
                with open(info_file) as f:
                    info = yaml.safe_load(f) or {}
                deps = info.get("build-deps", []) or []
                by_dist.setdefault(dist.distribution, set()).update(deps)
    return {
        "cache": {
            dist: {"packages": _normalize_for_cache(list(pkgs), excludes)}
            for dist, pkgs in sorted(by_dist.items())
        }
    }


@aliased_group("list-deps", chain=True)
def list_deps():
    """List build dependencies."""


_exclude_option = click.option(
    "--exclude",
    "exclude",
    multiple=True,
    metavar="REGEX",
    help=(
        "Regex matched against package names; matching packages are dropped. "
        "Repeatable. Appends to list-deps.exclude from builder.yml "
        f"(default {list(DEFAULT_EXCLUDES)})."
    ),
)


@list_deps.command()
@_exclude_option
@click.pass_obj
def run(obj: ContextObj, exclude: Tuple[str, ...]):
    """
    Run the list-deps stage and emit cache YAML to stdout.
    """
    _run_stage(obj)
    excludes = _resolve_excludes(obj, exclude)
    click.echo(
        yaml.safe_dump(
            _collect_cache_section(obj, excludes), default_flow_style=False
        ),
        nl=False,
    )


@list_deps.command()
@_exclude_option
@click.pass_obj
def show(obj: ContextObj, exclude: Tuple[str, ...]):
    """
    Emit cache YAML to stdout from existing artifacts (no stage run).
    """
    excludes = _resolve_excludes(obj, exclude)
    click.echo(
        yaml.safe_dump(
            _collect_cache_section(obj, excludes), default_flow_style=False
        ),
        nl=False,
    )


@list_deps.command()
@click.argument(
    "path",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
)
@_exclude_option
@click.pass_obj
def update(obj: ContextObj, path: Path, exclude: Tuple[str, ...]):
    """
    Merge cache.<dist>.packages into the given builder.yml (in-place).

    Writes a .bak backup first. Comments and formatting are lost on rewrite.
    """
    excludes = _resolve_excludes(obj, exclude)
    cache_section = _collect_cache_section(obj, excludes)
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    QubesBuilderLogger.warning(
        f"Backed up '{path}' to '{backup}'. "
        f"Comments/formatting in '{path}' will be lost on rewrite."
    )
    with open(path) as f:
        existing = yaml.safe_load(f) or {}
    existing_cache = existing.get("cache") or {}
    for dist_name, new_dist in cache_section["cache"].items():
        old_pkgs = (existing_cache.get(dist_name) or {}).get("packages") or []
        merged_pkgs = sorted(
            {str(p) for p in old_pkgs} | {str(p) for p in new_dist["packages"]}
        )
        existing_cache.setdefault(dist_name, {})["packages"] = [
            SingleQuoted(p) for p in merged_pkgs
        ]
    # Re-quote all other dists for a consistent format.
    for dist_body in existing_cache.values():
        if not isinstance(dist_body, dict):
            continue
        pkgs = dist_body.get("packages")
        if pkgs:
            dist_body["packages"] = [SingleQuoted(str(p)) for p in pkgs]
    existing["cache"] = existing_cache
    with open(path, "w") as f:
        yaml.safe_dump(existing, f, default_flow_style=False, sort_keys=False)
    click.echo(f"Updated {path}")


list_deps.add_command(run)
list_deps.add_command(show)
list_deps.add_command(update)
