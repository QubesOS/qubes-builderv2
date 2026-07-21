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

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from qubesbuilder.common import PROJECT_PATH, VerificationMode
from qubesbuilder.config import Config
from qubesbuilder.exc import QubesBuilderError
from qubesbuilder.log import QubesBuilderLogger

DEFAULT_URL = "https://github.com/QubesOS/qubes-builderv2"
DEFAULT_BRANCH = "main"

# Keys shipped with the fetch plugin. Like a component fetch, these are only the
# available keys, trust is decided by the maintainers list.
FETCH_KEYS_DIR = PROJECT_PATH / "qubesbuilder" / "plugins" / "fetch" / "keys"

# Min seconds between remote checks during builds.
DEFAULT_CHECK_INTERVAL = 86400

# Set to disable the automatic build-time update check (CI/tests).
NO_UPDATE_CHECK_ENV = "QUBES_BUILDER_NO_UPDATE_CHECK"


class SelfUpgradeError(QubesBuilderError):
    pass


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _is_clean_worktree(repo: Path) -> bool:
    status = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return status.strip() == ""


def _collect_keys(config: Config, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    key_dirs = [FETCH_KEYS_DIR]
    for key_dir_str in config.get("key-dirs", []):
        key_dir = Path(key_dir_str)
        if not key_dir.is_absolute():
            key_dir = config.get_conf_path().parent.joinpath(key_dir)
        key_dirs.append(key_dir)
    for key_dir in key_dirs:
        if not key_dir.is_dir():
            continue
        for key_file in key_dir.iterdir():
            if key_file.suffix != ".asc":
                continue
            shutil.copy2(key_file, dest / key_file.name)


def _parse_verification_mode(value: str) -> VerificationMode:
    # Same raw values as the per-component 'verification-mode'.
    try:
        return VerificationMode(value)
    except ValueError:
        accepted = ", ".join(sorted(m.value for m in VerificationMode))
        raise SelfUpgradeError(
            f"Invalid self-upgrade verification-mode '{value}'. "
            f"Accepted values: {accepted}."
        )


def _resolve_self_config(config: Config, repo: Path = PROJECT_PATH) -> dict:
    self_conf = config.get("self-upgrade", {}) or {}
    url = self_conf.get("url", DEFAULT_URL)
    configured_branch = self_conf.get("branch")
    branch = configured_branch or _current_branch(repo) or DEFAULT_BRANCH
    git_maintainers = config.get("git", {}).get("maintainers", []) or []
    maintainers = self_conf.get("maintainers", git_maintainers)
    verification_mode = _parse_verification_mode(
        self_conf.get("verification-mode", VerificationMode.SignedTag.value)
    )
    min_distinct_maintainers = int(
        self_conf.get(
            "min-distinct-maintainers",
            config.get("min-distinct-maintainers", 1),
        )
    )
    return {
        "url": url,
        "branch": branch,
        "branch_explicit": bool(configured_branch),
        "maintainers": maintainers,
        "verification_mode": verification_mode,
        "min_distinct_maintainers": min_distinct_maintainers,
    }


def _current_branch(repo: Path = PROJECT_PATH) -> Optional[str]:
    try:
        out = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)
    except subprocess.CalledProcessError:
        return None
    return out if out and out != "HEAD" else None


def _branch_tip(repo: Path, branch: str) -> Optional[str]:
    try:
        return _git(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=repo
        )
    except subprocess.CalledProcessError:
        return None


def _remote_head(url: str, branch: str, timeout: int = 15) -> Optional[str]:
    out = subprocess.run(
        ["git", "ls-remote", url, f"refs/heads/{branch}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    ).stdout.strip()
    if not out:
        return None
    return out.split()[0]


def _resolve_remote_branch(
    url: str, branch: str, explicit: bool
) -> Tuple[str, Optional[str]]:
    # Return (branch, head). An auto-detected local branch missing from the
    # remote (e.g. a dev branch) falls back to DEFAULT_BRANCH. An explicitly
    # configured branch stays strict so a typo still show error.
    head = _remote_head(url, branch)
    if head is None and not explicit and branch != DEFAULT_BRANCH:
        fallback = _remote_head(url, DEFAULT_BRANCH)
        if fallback is not None:
            return DEFAULT_BRANCH, fallback
    return branch, head


def _commit_in_history(commit: str, repo: Path, ref: str = "HEAD") -> bool:
    # True when `commit` is reachable from `ref` (up to date, or local commits on
    # top). A non-zero exit also covers an object we never fetched: not ours.
    return (
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                commit,
                ref,
            ],
            capture_output=True,
        ).returncode
        == 0
    )


def _local_compare_ref(repo: Path, branch: str) -> str:
    # Compare against the local branch when it exists, else the checked-out HEAD.
    # Keeps the comparison on the branch being checked even when another branch
    # (e.g. a dev branch) is checked out.
    exists = (
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
            ],
            capture_output=True,
        ).returncode
        == 0
    )
    return branch if exists else "HEAD"


def _check_state_path(config: Config) -> Path:
    return config.artifacts_dir / "self-upgrade-check.json"


def _read_check_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_check_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError:
        pass


def query_remote_update(
    config: Config, repo: Optional[Path] = None
) -> Tuple[Optional[str], bool, dict]:
    """
    Query the remote tip of the configured branch and whether it is already in
    the corresponding local branch's history.
    """
    repo = (repo or PROJECT_PATH).resolve()
    params = _resolve_self_config(config, repo)
    params["branch"], latest = _resolve_remote_branch(
        params["url"], params["branch"], params["branch_explicit"]
    )
    params["local_ref"] = _local_compare_ref(repo, params["branch"])
    behind = latest is not None and not _commit_in_history(
        latest, repo, params["local_ref"]
    )
    return latest, behind, params


def notify_if_update_available(
    config: Config, repo: Optional[Path] = None
) -> None:
    """
    Throttled, best-effort 'a newer qubes-builderv2 is available' notice.
    """
    log = QubesBuilderLogger.getChild("self-upgrade")
    if os.environ.get(NO_UPDATE_CHECK_ENV):
        return
    self_conf = config.get("self-upgrade", {}) or {}
    if not self_conf.get("check-for-updates", True):
        return
    repo = (repo or PROJECT_PATH).resolve()
    if not (repo / ".git").is_dir():
        return

    interval = int(self_conf.get("check-interval", DEFAULT_CHECK_INTERVAL))
    state_path = _check_state_path(config)
    state = _read_check_state(state_path)
    now = time.time()
    if interval > 0 and now - state.get("last_check", 0) < interval:
        return

    latest: Optional[str] = None
    behind = False
    params: Optional[dict] = None
    try:
        latest, behind, params = query_remote_update(config, repo=repo)
    except Exception as e:  # never break a build over an update check
        log.debug(f"Skipping self-upgrade check: {e}")

    # Record the attempt (even on failure) so we back off the full interval.
    state["last_check"] = now
    if latest:
        state["latest_commit"] = latest
    _write_check_state(state_path, state)

    if behind and latest and params:
        log.info(
            f"A newer qubes-builderv2 is available on '{params['branch']}' "
            f"({latest[:12]}). Run 'qb self upgrade' to update. It verifies "
            f"signatures before applying."
        )


def run_self_check(config: Config, repo: Optional[Path] = None) -> bool:
    """
    Manual update check (ignores the throttle, never touches the tree). Returns
    True when an update is available.
    """
    log = QubesBuilderLogger.getChild("self-upgrade")
    repo = (repo or PROJECT_PATH).resolve()
    if not (repo / ".git").is_dir():
        raise SelfUpgradeError(
            f"{repo} is not a git working tree. Cannot check for updates."
        )
    try:
        latest, behind, params = query_remote_update(config, repo=repo)
    except subprocess.TimeoutExpired as e:
        raise SelfUpgradeError("Timed out contacting the remote.") from e
    except subprocess.CalledProcessError as e:
        raise SelfUpgradeError(
            f"Failed to query remote: git exited with {e.returncode}"
        ) from e

    # Reset the throttle so the automatic check does not immediately repeat.
    state_path = _check_state_path(config)
    state = _read_check_state(state_path)
    state["last_check"] = time.time()
    if latest:
        state["latest_commit"] = latest
    _write_check_state(state_path, state)

    if not latest:
        raise SelfUpgradeError(
            f"Remote '{params['url']}' has no branch '{params['branch']}'."
        )

    local_ref = params.get("local_ref", "HEAD")
    current = _git("rev-parse", local_ref, cwd=repo)
    ref_label = "HEAD" if local_ref == "HEAD" else f"local {local_ref}"
    if behind:
        log.info(
            f"Update available on '{params['branch']}': {latest} "
            f"({ref_label} {current}). Run 'qb self upgrade' to apply."
        )
    else:
        log.info(
            f"qubes-builderv2 is up to date on '{params['branch']}' "
            f"({ref_label} {current})."
        )
    return behind


def run_self_upgrade(
    config: Config,
    dry_run: bool = False,
    repo: Optional[Path] = None,
) -> None:
    log = QubesBuilderLogger.getChild("self-upgrade")
    project = (repo or PROJECT_PATH).resolve()

    if not (project / ".git").is_dir():
        raise SelfUpgradeError(
            f"{project} is not a git working tree. Cannot self-upgrade."
        )

    if not _is_clean_worktree(project):
        raise SelfUpgradeError(
            f"Working tree at {project} has uncommitted changes. "
            "Commit or stash them before self-upgrading."
        )

    params = _resolve_self_config(config, project)
    if not params["maintainers"] and params["verification_mode"] != (
        VerificationMode.Insecure
    ):
        raise SelfUpgradeError(
            "No maintainer keys configured for self-upgrade. Set "
            "'self-upgrade.maintainers' (or 'git.maintainers') in builder.yml, "
            "or explicitly set "
            "'self-upgrade.verification-mode: insecure-skip-checking'."
        )

    for keyid in params["maintainers"]:
        if not re.match(r"^[a-fA-F0-9]{40}$", keyid):
            raise SelfUpgradeError(f"Invalid maintainer key id: {keyid}")

    # Track DEFAULT_BRANCH when an auto-detected local branch is absent from the
    # remote (mirrors 'qb self check'). Let the fetch script report real errors.
    try:
        params["branch"], _ = _resolve_remote_branch(
            params["url"], params["branch"], params["branch_explicit"]
        )
    except subprocess.SubprocessError:
        pass

    script = (
        PROJECT_PATH
        / "qubesbuilder"
        / "plugins"
        / "fetch"
        / "scripts"
        / "get-and-verify-source.py"
    )
    if not script.is_file():
        raise SelfUpgradeError(f"Cannot find fetch script: {script}")

    log.info(
        f"Self-upgrading {project} from {params['url']} "
        f"(branch={params['branch']}, "
        f"verification={params['verification_mode'].value})"
    )

    with tempfile.TemporaryDirectory(prefix="qb-self-upgrade-") as tmp:
        tmp_path = Path(tmp)
        keys_dir = tmp_path / "keys"
        keyring_dir = tmp_path / "keyring"
        _collect_keys(config, keys_dir)

        cmd: List[str] = [
            str(script),
            params["url"],
            str(project),
            str(keyring_dir),
            str(keys_dir),
            "--git-branch",
            params["branch"],
            "--minimum-distinct-maintainers",
            str(params["min_distinct_maintainers"]),
        ]
        for maintainer in params["maintainers"]:
            cmd += ["--maintainer", maintainer]
        if params["verification_mode"] == VerificationMode.Insecure:
            cmd += ["--insecure-skip-checking"]
        elif params["verification_mode"] == VerificationMode.SignedCommit:
            cmd += ["--less-secure-signed-commits-sufficient"]

        if dry_run:
            log.info("Dry run, would execute: " + " ".join(cmd))
            return

        original_branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=project)
        old_head = _git("rev-parse", "HEAD", cwd=project)
        old_branch_tip = _branch_tip(project, params["branch"])
        # The fetch script checks out params["branch"]. If we started elsewhere
        # (e.g. a dev branch), switch back so the checkout is left where it was.
        need_restore = original_branch != params["branch"]
        upgrade_error: Optional[subprocess.CalledProcessError] = None
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            upgrade_error = e
        finally:
            restored = False
            if need_restore:
                restore_to = (
                    original_branch if original_branch != "HEAD" else old_head
                )
                try:
                    _git("checkout", "-q", restore_to, cwd=project)
                    restored = True
                except subprocess.CalledProcessError as e:
                    log.warning(
                        f"Upgraded, but could not restore branch "
                        f"'{original_branch}'. ({e})"
                    )
        if upgrade_error is not None:
            raise SelfUpgradeError(
                f"Self-upgrade failed: {script.name} exited with "
                f"{upgrade_error.returncode}"
            ) from upgrade_error
        new_branch_tip = _branch_tip(project, params["branch"])
        new_head = _git("rev-parse", "HEAD", cwd=project)

    if restored:
        if old_branch_tip == new_branch_tip:
            log.info(
                f"'{params['branch']}' already up to date at {new_branch_tip}. "
                f"Staying on '{original_branch}'."
            )
            return
        rule = "=" * 70
        log.warning(
            "\n".join(
                [
                    "",
                    rule,
                    f"Updated '{params['branch']}' {old_branch_tip or '(new)'} -> {new_branch_tip}.",
                    f"You are on branch '{original_branch}', NOT '{params['branch']}'.",
                    f"Your branch '{original_branch}' was NOT changed and does not have the update yet.",
                    f"To get it, rebase or merge '{params['branch']}' into '{original_branch}'.",
                    rule,
                ]
            )
        )
        return

    if old_head == new_head:
        log.info(f"Already up to date at {new_head}.")
        return

    log.info(f"Updated {old_head} -> {new_head}.")
    log.warning(
        "qubes-builderv2 was upgraded in place. The running Python process "
        "still holds the previous source loaded. Re-run your next 'qb' "
        "invocation to use the new code. Do NOT combine 'qb self upgrade' "
        "with other subcommands."
    )
