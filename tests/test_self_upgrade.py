import json
import logging
import os
import subprocess
import time

import pytest

from qubesbuilder.config import Config
from qubesbuilder.self_upgrade import (
    FETCH_KEYS_DIR,
    SelfUpgradeError,
    _collect_keys,
    notify_if_update_available,
    query_remote_update,
    run_self_check,
    run_self_upgrade,
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "titi",
    "GIT_AUTHOR_EMAIL": "titi@toto.com",
    "GIT_COMMITTER_NAME": "titi",
    "GIT_COMMITTER_EMAIL": "titi@toto.com",
}


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    ).stdout.strip()


def _commit(repo, name):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    sha_a = _commit(remote, "a.txt")
    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", str(remote), str(local)],
        check=True,
        capture_output=True,
    )
    # Advance the remote past what the clone has.
    sha_b = _commit(remote, "b.txt")
    return {
        "tmp": tmp_path,
        "remote": remote,
        "local": local,
        "sha_a": sha_a,
        "sha_b": sha_b,
    }


def _config(tmp_path, remote, branch="main", **self_upgrade):
    lines = [
        f"artifacts-dir: {tmp_path / 'artifacts'}",
        "self-upgrade:",
        f"  url: {remote}",
    ]
    if branch is not None:
        lines.append(f"  branch: {branch}")
    for key, value in self_upgrade.items():
        lines.append(f"  {key}: {value}")
    conf = tmp_path / "builder.yml"
    conf.write_text("\n".join(lines) + "\n")
    return Config(conf_file=str(conf))


def test_query_behind(repos):
    cfg = _config(repos["tmp"], repos["remote"])
    latest, behind, _ = query_remote_update(cfg, repo=repos["local"])
    assert latest == repos["sha_b"]
    assert behind is True


def test_query_up_to_date(repos):
    _git(repos["local"], "fetch", "origin")
    _git(repos["local"], "reset", "--hard", repos["sha_b"])
    cfg = _config(repos["tmp"], repos["remote"])
    latest, behind, _ = query_remote_update(cfg, repo=repos["local"])
    assert latest == repos["sha_b"]
    assert behind is False


def test_query_local_commits_on_top(repos):
    # Remote tip is an ancestor of HEAD: not behind ('not just on top').
    _git(repos["local"], "fetch", "origin")
    _git(repos["local"], "reset", "--hard", repos["sha_b"])
    _commit(repos["local"], "local.txt")
    cfg = _config(repos["tmp"], repos["remote"])
    _, behind, _ = query_remote_update(cfg, repo=repos["local"])
    assert behind is False


def test_run_self_check_behind(repos):
    cfg = _config(repos["tmp"], repos["remote"])
    assert run_self_check(cfg, repo=repos["local"]) is True
    state = json.loads(
        (cfg.artifacts_dir / "self-upgrade-check.json").read_text()
    )
    assert state["latest_commit"] == repos["sha_b"]
    assert state["last_check"] > 0


def test_run_self_check_unknown_branch(repos):
    # An explicitly configured branch stays strict (no silent fallback).
    cfg = _config(repos["tmp"], repos["remote"], branch="does-not-exist")
    with pytest.raises(SelfUpgradeError):
        run_self_check(cfg, repo=repos["local"])


def test_query_auto_branch_falls_back_to_main(repos):
    # Local checkout is on a dev branch absent from the remote and no branch is
    # configured: the check falls back to main rather than erroring.
    _git(repos["local"], "checkout", "-q", "-b", "devel-local")
    cfg = _config(repos["tmp"], repos["remote"], branch=None)
    latest, behind, params = query_remote_update(cfg, repo=repos["local"])
    assert params["branch"] == "main"
    assert latest == repos["sha_b"]
    assert behind is True


def test_run_self_check_auto_branch_falls_back_to_main(repos):
    _git(repos["local"], "checkout", "-q", "-b", "devel-local")
    cfg = _config(repos["tmp"], repos["remote"], branch=None)
    assert run_self_check(cfg, repo=repos["local"]) is True


def test_query_explicit_branch_no_fallback(repos):
    cfg = _config(repos["tmp"], repos["remote"], branch="does-not-exist")
    latest, behind, params = query_remote_update(cfg, repo=repos["local"])
    assert latest is None
    assert behind is False
    assert params["branch"] == "does-not-exist"


def test_query_compares_local_branch_not_head(repos):
    # Local 'main' is up to date with the remote, but HEAD is on a divergent dev
    # branch that lacks the tip. Comparing against local 'main' -> not behind.
    local = repos["local"]
    _git(local, "fetch", "origin")
    _git(local, "checkout", "-q", "main")
    _git(local, "reset", "--hard", repos["sha_b"])
    _git(local, "checkout", "-q", "-b", "devel", repos["sha_a"])
    cfg = _config(repos["tmp"], repos["remote"], branch="main")
    latest, behind, params = query_remote_update(cfg, repo=local)
    assert latest == repos["sha_b"]
    assert params["local_ref"] == "main"
    assert behind is False


def test_query_local_branch_behind_on_dev_branch(repos):
    # Mirrors the reported case: local 'main' is behind upstream while HEAD sits
    # on a divergent dev branch. We compare against local 'main' -> behind.
    local = repos["local"]
    _git(local, "checkout", "-q", "-b", "devel")
    _commit(local, "devwork.txt")
    cfg = _config(repos["tmp"], repos["remote"], branch="main")
    latest, behind, params = query_remote_update(cfg, repo=local)
    assert latest == repos["sha_b"]
    assert params["local_ref"] == "main"
    assert behind is True


def test_query_no_local_branch_uses_head(repos):
    # No local branch for the checked branch -> fall back to HEAD.
    local = repos["local"]
    _git(local, "checkout", "-q", "-b", "other")
    _git(local, "branch", "-D", "main")
    cfg = _config(repos["tmp"], repos["remote"], branch="main")
    latest, behind, params = query_remote_update(cfg, repo=local)
    assert params["local_ref"] == "HEAD"
    assert behind is True


def test_notify_behind_emits_and_writes_state(repos, caplog, monkeypatch):
    monkeypatch.delenv("QUBES_BUILDER_NO_UPDATE_CHECK", raising=False)
    cfg = _config(repos["tmp"], repos["remote"], **{"check-interval": 0})
    with caplog.at_level(logging.INFO, logger="qb"):
        notify_if_update_available(cfg, repo=repos["local"])
    assert any("newer qubes-builderv2" in r.message for r in caplog.records)
    state = json.loads(
        (cfg.artifacts_dir / "self-upgrade-check.json").read_text()
    )
    assert state["latest_commit"] == repos["sha_b"]


def test_notify_throttled(repos, monkeypatch):
    monkeypatch.delenv("QUBES_BUILDER_NO_UPDATE_CHECK", raising=False)
    cfg = _config(repos["tmp"], repos["remote"], **{"check-interval": 100000})
    state_path = cfg.artifacts_dir / "self-upgrade-check.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    last_check = time.time() - 1
    state_path.write_text(
        json.dumps({"last_check": last_check, "latest_commit": "deadbeef"})
    )
    notify_if_update_available(cfg, repo=repos["local"])
    # Still inside the window: no remote contact, cached state untouched.
    state = json.loads(state_path.read_text())
    assert state["latest_commit"] == "deadbeef"
    assert state["last_check"] == last_check


def test_notify_env_gate(repos, monkeypatch):
    monkeypatch.setenv("QUBES_BUILDER_NO_UPDATE_CHECK", "1")
    cfg = _config(repos["tmp"], repos["remote"], **{"check-interval": 0})
    notify_if_update_available(cfg, repo=repos["local"])
    assert not (cfg.artifacts_dir / "self-upgrade-check.json").exists()


def test_notify_config_disabled(repos, monkeypatch):
    monkeypatch.delenv("QUBES_BUILDER_NO_UPDATE_CHECK", raising=False)
    cfg = _config(
        repos["tmp"],
        repos["remote"],
        **{"check-for-updates": "false", "check-interval": 0},
    )
    notify_if_update_available(cfg, repo=repos["local"])
    assert not (cfg.artifacts_dir / "self-upgrade-check.json").exists()


def _upgrade_config(
    tmp_path,
    remote,
    *,
    branch="main",
    verification_mode=None,
    maintainers=None,
    key_dirs=None,
):
    lines = [f"artifacts-dir: {tmp_path / 'artifacts'}"]
    lines.append("self-upgrade:")
    lines.append(f"  url: {remote}")
    if branch is not None:
        lines.append(f"  branch: {branch}")
    if verification_mode:
        lines.append(f"  verification-mode: {verification_mode}")
    if maintainers is not None:
        lines.append("  maintainers:")
        for m in maintainers:
            lines.append(f"    - {m}")
    if key_dirs:
        lines.append("key-dirs:")
        for d in key_dirs:
            lines.append(f"  - {d}")
    conf = tmp_path / "builder-upgrade.yml"
    conf.write_text("\n".join(lines) + "\n")
    return Config(conf_file=str(conf))


def test_upgrade_not_a_git_tree(repos):
    plain = repos["tmp"] / "plain"
    plain.mkdir()
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        verification_mode="insecure-skip-checking",
    )
    with pytest.raises(SelfUpgradeError, match="not a git working tree"):
        run_self_upgrade(cfg, repo=plain)


def test_upgrade_dirty_worktree_blocks(repos):
    # A tracked, uncommitted change blocks the upgrade.
    (repos["local"] / "a.txt").write_text("modified tracked file")
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        verification_mode="insecure-skip-checking",
    )
    with pytest.raises(SelfUpgradeError, match="uncommitted changes"):
        run_self_upgrade(cfg, repo=repos["local"])


def test_upgrade_untracked_files_allowed(repos):
    # Untracked files (e.g. build leftovers) do not block the upgrade.
    (repos["local"] / "untracked.txt").write_text("not tracked")
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        verification_mode="insecure-skip-checking",
    )
    run_self_upgrade(cfg, repo=repos["local"])
    assert _git(repos["local"], "rev-parse", "HEAD") == repos["sha_b"]


def test_upgrade_requires_maintainers(repos):
    cfg = _upgrade_config(repos["tmp"], repos["remote"])
    with pytest.raises(SelfUpgradeError, match="No maintainer keys"):
        run_self_upgrade(cfg, repo=repos["local"])


def test_upgrade_invalid_maintainer(repos):
    cfg = _upgrade_config(
        repos["tmp"], repos["remote"], maintainers=["not-a-fingerprint"]
    )
    with pytest.raises(SelfUpgradeError, match="Invalid maintainer key id"):
        run_self_upgrade(cfg, repo=repos["local"])


def test_upgrade_invalid_verification_mode(repos):
    cfg = _upgrade_config(
        repos["tmp"], repos["remote"], verification_mode="bogus"
    )
    with pytest.raises(SelfUpgradeError, match="verification-mode"):
        run_self_upgrade(cfg, repo=repos["local"])


def test_upgrade_insecure_skips_maintainer_requirement(repos):
    # insecure-skip-checking skips the maintainer requirement. Dry-run no-op.
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        verification_mode="insecure-skip-checking",
    )
    run_self_upgrade(cfg, repo=repos["local"], dry_run=True)
    assert _git(repos["local"], "rev-parse", "HEAD") == repos["sha_a"]


def test_upgrade_fast_forward(repos):
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        verification_mode="insecure-skip-checking",
    )
    assert _git(repos["local"], "rev-parse", "HEAD") == repos["sha_a"]
    run_self_upgrade(cfg, repo=repos["local"])
    assert _git(repos["local"], "rev-parse", "HEAD") == repos["sha_b"]


def test_upgrade_already_up_to_date(repos):
    _git(repos["local"], "fetch", "origin")
    _git(repos["local"], "merge", "--ff-only", repos["sha_b"])
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        verification_mode="insecure-skip-checking",
    )
    run_self_upgrade(cfg, repo=repos["local"])
    assert _git(repos["local"], "rev-parse", "HEAD") == repos["sha_b"]


def test_collect_keys_seeds_bundled_developer_keys(repos):
    # Regression: self-upgrade must include the bundled fetch/keys, otherwise
    # the maintainer key is missing and get-and-verify-source fails.
    cfg = _config(repos["tmp"], repos["remote"])
    dest = repos["tmp"] / "keys"
    _collect_keys(cfg, dest)
    bundled = {p.name for p in FETCH_KEYS_DIR.glob("*.asc")}
    collected = {p.name for p in dest.glob("*.asc")}
    assert bundled
    assert bundled <= collected


def test_collect_keys_layers_key_dirs(repos):
    extra = repos["tmp"] / "extrakeys"
    extra.mkdir()
    key_name = "deadbeef123456789abcdef.asc"
    (extra / key_name).write_text("KEY")
    conf = repos["tmp"] / "builder-keys.yml"
    conf.write_text(
        f"artifacts-dir: {repos['tmp'] / 'artifacts'}\n"
        "key-dirs:\n"
        "  - extrakeys\n"
    )
    cfg = Config(conf_file=str(conf))
    dest = repos["tmp"] / "keys2"
    _collect_keys(cfg, dest)
    names = {p.name for p in dest.glob("*.asc")}
    assert key_name in names
    assert "qubes-developers-keys.asc" in names


def test_upgrade_auto_branch_falls_back_to_main(repos):
    # On a dev branch absent from the remote with no configured branch, the
    # upgrade falls back to main, advances it, and restores the dev branch.
    _git(repos["local"], "checkout", "-q", "-b", "devel-local")
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        branch=None,
        verification_mode="insecure-skip-checking",
    )
    run_self_upgrade(cfg, repo=repos["local"])
    assert _git(repos["local"], "rev-parse", "main") == repos["sha_b"]
    assert (
        _git(repos["local"], "rev-parse", "--abbrev-ref", "HEAD")
        == "devel-local"
    )


def test_upgrade_restores_original_branch(repos, caplog):
    # On a divergent dev branch, upgrading 'main' must leave us back on the dev
    # branch (untouched) while local 'main' advances to the upstream tip.
    local = repos["local"]
    _git(local, "checkout", "-q", "-b", "devel")
    devel_head = _commit(local, "devwork.txt")
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        branch="main",
        verification_mode="insecure-skip-checking",
    )
    with caplog.at_level(logging.INFO, logger="qb"):
        run_self_upgrade(cfg, repo=local)
    assert _git(local, "rev-parse", "--abbrev-ref", "HEAD") == "devel"
    assert _git(local, "rev-parse", "HEAD") == devel_head
    assert _git(local, "rev-parse", "main") == repos["sha_b"]
    warnings = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("on branch 'devel', NOT 'main'" in m for m in warnings)
    assert any("was NOT changed" in m for m in warnings)


def test_upgrade_auto_fallback_restores_dev_branch(repos):
    # The reported case: on devel (not on the remote), upgrade falls back to
    # main, advances local main, and returns us to devel.
    local = repos["local"]
    _git(local, "checkout", "-q", "-b", "devel20260605")
    devel_head = _commit(local, "devwork.txt")
    cfg = _upgrade_config(
        repos["tmp"],
        repos["remote"],
        branch=None,
        verification_mode="insecure-skip-checking",
    )
    run_self_upgrade(cfg, repo=local)
    assert _git(local, "rev-parse", "--abbrev-ref", "HEAD") == "devel20260605"
    assert _git(local, "rev-parse", "HEAD") == devel_head
    assert _git(local, "rev-parse", "main") == repos["sha_b"]


# tests/gnupg keyring.
TEST_KEY = "8B080B3E649B153AA44FE43E722F2B7B164FDEF7"


def _git_env(repo, env, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _signed_repos(home, *, sign_tag):
    # Build a local 'remote' with a new commit, optionally with a signed tag on
    # it, plus a clone to upgrade. When signing, both commits and the tag are
    # signed with the tests/gnupg key.
    gnupg = str(home / "gnupg")
    env = {**os.environ, "GNUPGHOME": gnupg, "HOME": str(home)}
    remote = home / "remote"
    remote.mkdir()
    _git_env(remote, env, "init", "-q", "-b", "main")
    _git_env(remote, env, "config", "user.signingkey", TEST_KEY)
    if sign_tag:
        _git_env(remote, env, "config", "commit.gpgsign", "true")
    (remote / "a.txt").write_text("a")
    _git_env(remote, env, "add", "a.txt")
    _git_env(remote, env, "commit", "-q", "-m", "add a")
    sha_a = _git_env(remote, env, "rev-parse", "HEAD")
    local = home / "local"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(local)],
        check=True,
        capture_output=True,
        env=env,
    )
    (remote / "b.txt").write_text("b")
    _git_env(remote, env, "add", "b.txt")
    _git_env(remote, env, "commit", "-q", "-m", "add b")
    sha_b = _git_env(remote, env, "rev-parse", "HEAD")
    if sign_tag:
        _git_env(remote, env, "tag", "-s", "v1.0", "-m", "release 1.0")
    keys = home / "keys"
    keys.mkdir()
    pub = subprocess.run(
        ["gpg", "--export", "--armor", TEST_KEY],
        capture_output=True,
        text=True,
        check=True,
        env={"GNUPGHOME": gnupg},
    ).stdout
    (keys / f"{TEST_KEY}.asc").write_text(pub)
    return {
        "home": home,
        "remote": remote,
        "local": local,
        "sha_a": sha_a,
        "sha_b": sha_b,
        "keys": keys,
    }


def test_upgrade_signed_tag_verifies_and_fast_forwards(home_directory):
    s = _signed_repos(home_directory, sign_tag=True)
    cfg = _upgrade_config(
        s["home"], s["remote"], maintainers=[TEST_KEY], key_dirs=[s["keys"]]
    )
    run_self_upgrade(cfg, repo=s["local"])
    assert _git(s["local"], "rev-parse", "HEAD") == s["sha_b"]


def test_upgrade_unsigned_rejected_and_branch_unchanged(home_directory):
    # No signed tag on the new commit: verification fails and the local branch
    # is left unchanged.
    s = _signed_repos(home_directory, sign_tag=False)
    cfg = _upgrade_config(
        s["home"], s["remote"], maintainers=[TEST_KEY], key_dirs=[s["keys"]]
    )
    with pytest.raises(SelfUpgradeError):
        run_self_upgrade(cfg, repo=s["local"])
    assert _git(s["local"], "rev-parse", "HEAD") == s["sha_a"]
    assert _git(s["local"], "rev-parse", "main") == s["sha_a"]
