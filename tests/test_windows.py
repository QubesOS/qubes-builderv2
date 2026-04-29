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


import subprocess
from pathlib import Path

import pytest
import yaml

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.windows import BaseWindowsExecutor
from qubesbuilder.plugins import PackagePath
from qubesbuilder.plugins import PluginError
from qubesbuilder.plugins.build import BuildError
from qubesbuilder.plugins.build_windows import (
    WinArtifactKind,
    WinArtifactSet,
    WindowsBuildPlugin,
    clean_local_repository,
    mangle_key_name,
    provision_local_repository,
)
from qubesbuilder.plugins.chroot_windows import WindowsChrootPlugin
from qubesbuilder.plugins.publish import PublishError
from qubesbuilder.plugins.publish_windows import WindowsPublishPlugin
from qubesbuilder.plugins.sign_windows import WindowsSignPlugin
from qubesbuilder.plugins.source_windows import WindowsSourcePlugin


WIN_QUBESBUILDER = """\
vm:
  windows:
    build:
      - component.sln
    bin:
      - component.exe
      - component.dll
    lib:
      - component.lib
"""

BUILDER_CONF = """\
executor:
  type: local
artifacts-dir: {artifacts_dir}
repository-publish:
  components: current-testing
"""

BUILD_MANGLE = "component.sln"


#
# Fixtures
#


@pytest.fixture
def workdir(tmp_path):
    src = tmp_path / "test-component"
    src.mkdir()
    (src / "version").write_text("1.0")
    (src / "rel").write_text("1")
    (src / ".qubesbuilder").write_text(WIN_QUBESBUILDER)

    # Initialize a git repo so get_source_commit_hash() succeeds.
    for cmd in [
        ["git", "init", str(src)],
        ["git", "-C", str(src), "config", "user.email", "test@test.com"],
        ["git", "-C", str(src), "config", "user.name", "Test"],
        ["git", "-C", str(src), "config", "commit.gpgsign", "false"],
        ["git", "-C", str(src), "add", "."],
        ["git", "-C", str(src), "commit", "-m", "init"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)

    return tmp_path


@pytest.fixture
def config(workdir):
    artifacts_dir = workdir / "artifacts"
    artifacts_dir.mkdir()
    conf_file = workdir / "builder.yml"
    conf_file.write_text(BUILDER_CONF.format(artifacts_dir=artifacts_dir))
    return Config(conf_file)


@pytest.fixture
def win_dist():
    return QubesDistribution("vm-win10")


@pytest.fixture
def component(workdir):
    return QubesComponent(workdir / "test-component")


#
# Helpers
#


def _write_build_artifact(
    config, component, dist, build_mangle, source_hash="tititototata"
):
    """
    Simulate a completed Windows build by writing a build artifact YAML that
    mirrors what WindowsBuildPlugin.run() would produce: binary (exe, dll)
    and library outputs, plus a source hash. Also creates dummy binary files
    so publish_windows can hardlink them.
    """
    build_dir = (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "build"
    )
    build_dir.mkdir(parents=True, exist_ok=True)

    # Satisfy source checks done by job dependencies in sign/publish plugins.
    (config.sources_dir / component.name).mkdir(parents=True, exist_ok=True)

    bin_dir = build_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "component.exe").write_bytes(b"MZ")
    (bin_dir / "component.dll").write_bytes(b"MZ")

    info = {
        "files": {
            "bin": ["component.exe", "component.dll"],
            "inc": [],
            "lib": ["component.lib"],
        },
        "source-hash": source_hash,
    }
    artifact_path = build_dir / f"{build_mangle}.build.yml"
    artifact_path.write_text(yaml.safe_dump(info))
    return artifact_path


def _write_fetch_artifact(config, component, info=None):
    fetch_dir = (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / "nodist"
        / "fetch"
    )
    fetch_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = fetch_dir / "source.fetch.yml"
    artifact_path.write_text(yaml.safe_dump(info or {"files": []}))
    return artifact_path


def _prepare_source_dependencies(config, component, dist, fetch_info=None):
    WindowsChrootPlugin(dist=dist, config=config, stage="init-cache").run()
    _write_fetch_artifact(config, component, info=fetch_info)
    (config.sources_dir / component.name).mkdir(parents=True, exist_ok=True)


def _source_artifact_path(config, component, dist):
    return (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "prep"
        / f"{component.name}.prep.yml"
    )


def _source_build_artifact_path(config, component, dist, build_mangle):
    return (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "prep"
        / f"{build_mangle}.prep.yml"
    )


def _sign_artifact_path(config, component, dist, build_mangle):
    return (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "sign"
        / f"{build_mangle}.sign.yml"
    )


def _publish_artifact_path(config, component, dist, build_mangle):
    return (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "publish"
        / f"{build_mangle}.publish.yml"
    )


def _published_component_dir(config, component, dist, repository):
    return (
        config.repository_publish_dir
        / dist.type
        / config.qubes_release
        / repository
        / dist.package_set
        / dist.name
        / f"{component.name}_{component.version}"
    )


def _init_cache_artifact_path(config, dist):
    return (
        config.cache_dir
        / "chroot"
        / dist.distribution
        / dist.nva
        / f"{dist.nva}.init-cache.yml"
    )


class DummyWindowsExecutor(BaseWindowsExecutor):
    def copy_in(self, *args, **kwargs):
        return None

    def copy_out(self, *args, **kwargs):
        return None

    def run(self, *args, **kwargs):
        return ""


class DummyPublishExecutor:
    def __init__(self, should_raise=False):
        self.should_raise = should_raise
        self.commands = []

    def run(self, cmd):
        if self.should_raise:
            raise ExecutorError("boom")
        self.commands.append(cmd)


#
# init-cache stage
#


def test_init_cache_creates_artifact(config, win_dist):
    plugin = WindowsChrootPlugin(
        dist=win_dist,
        config=config,
        stage="init-cache",
    )
    plugin.run()

    artifact = _init_cache_artifact_path(config, win_dist)
    assert artifact.exists(), f"Expected init-cache artifact at {artifact}"
    info = yaml.safe_load(artifact.read_text())
    assert info == {"packages": []}


def test_init_cache_reuses_existing_artifact(config, win_dist):
    plugin = WindowsChrootPlugin(
        dist=win_dist,
        config=config,
        stage="init-cache",
    )
    plugin.run()
    artifact = _init_cache_artifact_path(config, win_dist)
    mtime_first = artifact.stat().st_mtime

    plugin.run()  # second call, no force
    assert (
        artifact.stat().st_mtime == mtime_first
    ), "Artifact should not be overwritten without --force"


def test_init_cache_force_recreates_artifact(config, win_dist):
    plugin = WindowsChrootPlugin(
        dist=win_dist,
        config=config,
        stage="init-cache",
    )
    plugin.run()
    artifact = _init_cache_artifact_path(config, win_dist)
    artifact.write_text("toto")

    plugin.run(force=True)
    info = yaml.safe_load(artifact.read_text())
    assert info == {
        "packages": []
    }, "Artifact should be regenerated with correct content after --force"


#
# source stage
#


def test_source_prep_creates_artifacts(config, component, win_dist):
    fetch_info = {"files": ["source.tar.gz"], "custom": "kept"}
    _prepare_source_dependencies(config, component, win_dist, fetch_info)

    plugin = WindowsSourcePlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="prep",
    )
    plugin.run()

    source_artifact = _source_artifact_path(config, component, win_dist)
    assert source_artifact.exists()
    info = yaml.safe_load(source_artifact.read_text())
    assert info["custom"] == "kept"
    assert info["source-hash"] == component.get_source_hash()

    target_artifact = _source_build_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    )
    assert target_artifact.exists()
    assert yaml.safe_load(target_artifact.read_text()) == {"dummy": 1}


def test_source_prep_skips_when_source_hash_is_unchanged(
    config, component, win_dist
):
    _prepare_source_dependencies(config, component, win_dist)

    source_artifact = _source_artifact_path(config, component, win_dist)
    original = {
        "source-hash": component.get_source_hash(),
        "marker": "preserve",
    }
    source_artifact.parent.mkdir(parents=True, exist_ok=True)
    source_artifact.write_text(yaml.safe_dump(original))
    mtime_before = source_artifact.stat().st_mtime

    plugin = WindowsSourcePlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="prep",
    )
    plugin.run()

    assert source_artifact.stat().st_mtime == mtime_before
    assert yaml.safe_load(source_artifact.read_text()) == original


def test_source_prep_requires_init_cache_artifact(config, component, win_dist):
    _write_fetch_artifact(config, component)
    (config.sources_dir / component.name).mkdir(parents=True, exist_ok=True)

    plugin = WindowsSourcePlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="prep",
    )

    with pytest.raises(PluginError, match="Failed to retrieve artifact path"):
        plugin.run()


#
# build stage (mimicked)
#


def test_build_artifact_structure(config, component, win_dist):
    artifact_path = _write_build_artifact(
        config, component, win_dist, BUILD_MANGLE
    )
    assert artifact_path.exists(), f"Expected build artifact at {artifact_path}"

    info = yaml.safe_load(artifact_path.read_text())
    assert "files" in info, "Build artifact must have a 'files' key"
    assert "source-hash" in info, "Build artifact must have a 'source-hash' key"

    bins = info["files"]["bin"]
    assert "component.exe" in bins
    assert "component.dll" in bins
    assert "component.lib" in info["files"]["lib"]
    assert info["files"]["inc"] == []


#
# build_windows unit tests
#


def test_build_helpers_artifact_set_and_mangle():
    artifacts = WinArtifactSet()
    assert repr(WinArtifactKind.BIN) == "BIN"
    assert artifacts.get_kind(WinArtifactKind.BIN) == []

    artifacts.add(WinArtifactKind.BIN, "component.exe")
    assert artifacts.get_kind(WinArtifactKind.BIN) == ["component.exe"]
    assert "component.exe" in repr(artifacts)
    assert mangle_key_name("Qubes Windows Tools") == "Qubes__Windows__Tools"


def test_build_helpers_clean_local_repository(config, component, win_dist):
    repository_dir = config.repository_dir / win_dist.distribution
    repository_dir.mkdir(parents=True, exist_ok=True)
    component.get_version()

    current = repository_dir / f"{component.name}_{component.version}"
    old = repository_dir / f"{component.name}_0.9"
    current.mkdir(parents=True, exist_ok=True)
    old.mkdir(parents=True, exist_ok=True)

    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    clean_local_repository(
        plugin.log, repository_dir, component, win_dist, all_versions=False
    )
    assert not current.exists()
    assert old.exists()

    clean_local_repository(
        plugin.log, repository_dir, component, win_dist, all_versions=True
    )
    assert not old.exists()


def test_build_helpers_provision_local_repository(config, component, win_dist):
    repository_dir = config.repository_dir / win_dist.distribution
    build_artifacts_dir = (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / win_dist.distribution
        / "build"
    )
    for kind in WinArtifactKind:
        (build_artifacts_dir / kind).mkdir(parents=True, exist_ok=True)
    (build_artifacts_dir / "bin" / "component.exe").write_bytes(b"MZ")
    (build_artifacts_dir / "lib" / "component.lib").write_bytes(b"lib")
    (build_artifacts_dir / "sign.crt").write_bytes(b"crt")

    artifacts = WinArtifactSet()
    artifacts.add(WinArtifactKind.BIN, "component.exe")
    artifacts.add(WinArtifactKind.LIB, "component.lib")

    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    provision_local_repository(
        log=plugin.log,
        repository_dir=repository_dir,
        component=component,
        dist=win_dist,
        target=BUILD_MANGLE,
        artifacts=artifacts,
        build_artifacts_dir=build_artifacts_dir,
        test_sign=True,
    )

    target_dir = repository_dir / f"{component.name}_{component.version}"
    assert (target_dir / "bin" / "component.exe").exists()
    assert (target_dir / "lib" / "component.lib").exists()
    assert (target_dir / "sign.crt").exists()


def test_build_helpers_provision_local_repository_wraps_link_errors(
    config, component, win_dist
):
    repository_dir = config.repository_dir / win_dist.distribution
    build_artifacts_dir = (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / win_dist.distribution
        / "build"
    )
    (build_artifacts_dir / "bin").mkdir(parents=True, exist_ok=True)
    (build_artifacts_dir / "bin" / "component.exe").write_bytes(b"MZ")
    artifacts = WinArtifactSet()
    artifacts.add(WinArtifactKind.BIN, "component.exe")

    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    provision_local_repository(
        log=plugin.log,
        repository_dir=repository_dir,
        component=component,
        dist=win_dist,
        target=BUILD_MANGLE,
        artifacts=artifacts,
        build_artifacts_dir=build_artifacts_dir,
        test_sign=False,
    )

    with pytest.raises(
        BuildError, match="Failed to provision local repository"
    ):
        provision_local_repository(
            log=plugin.log,
            repository_dir=repository_dir,
            component=component,
            dist=win_dist,
            target=BUILD_MANGLE,
            artifacts=artifacts,
            build_artifacts_dir=build_artifacts_dir,
            test_sign=False,
        )


def test_build_sign_prep_and_sign_sign(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    calls = []

    def _fake_qrexec_call(**kwargs):
        calls.append(kwargs["service"])
        if "GetCert+" in kwargs["service"]:
            return b"cert"
        if "Sign+" in kwargs["service"]:
            return b"signed"
        return b""

    monkeypatch.setattr(
        "qubesbuilder.plugins.build_windows.qrexec_call", _fake_qrexec_call
    )
    cert = plugin.sign_prep("sign-vm", "Qubes Windows Tools", test_sign=True)
    assert cert == b"cert"
    assert any(
        "CreateKey+Qubes__Windows__Tools" in service for service in calls
    )
    assert any("GetCert+Qubes__Windows__Tools" in service for service in calls)

    file_path = config.artifacts_dir / "unsigned.bin"
    file_path.write_bytes(b"abc")
    signed = plugin.sign_sign("sign-vm", "Qubes Windows Tools", file_path)
    assert signed == b"signed"


def test_build_sign_delete_key_behavior(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    calls = []

    def _fake_qrexec_call(**kwargs):
        calls.append(kwargs["service"])
        if "QueryKey+" in kwargs["service"]:
            return b"Key 'Qubes__Windows__Tools' exists"
        return b""

    monkeypatch.setattr(
        "qubesbuilder.plugins.build_windows.qrexec_call", _fake_qrexec_call
    )
    plugin.sign_delete_key("sign-vm", "Qubes Windows Tools")
    assert any(
        "DeleteKey+Qubes__Windows__Tools" in service for service in calls
    )


def test_build_sign_delete_key_noop_when_missing(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    calls = []

    def _fake_qrexec_call(**kwargs):
        calls.append(kwargs["service"])
        return b"missing"

    monkeypatch.setattr(
        "qubesbuilder.plugins.build_windows.qrexec_call", _fake_qrexec_call
    )
    plugin.sign_delete_key("sign-vm", "Qubes Windows Tools")
    assert calls == ["qubesbuilder.WinSign.QueryKey+Qubes__Windows__Tools"]


def test_build_run_requires_windows_executor(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)

    with pytest.raises(BuildError, match="requires BaseWindowsExecutor"):
        plugin.run()


def test_build_run_requires_sign_qube_option(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)

    with pytest.raises(BuildError, match="'sign-qube' option not configured"):
        plugin.run()


def test_build_run_skips_when_source_hash_is_unchanged(
    config, component, win_dist, monkeypatch
):
    source_hash = component.get_source_hash()
    _write_build_artifact(
        config, component, win_dist, BUILD_MANGLE, source_hash=source_hash
    )
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)

    plugin.run()


def test_build_run_returns_early_when_no_packages(config, component, win_dist):
    component.has_packages = False
    (config.sources_dir / component.name).mkdir(parents=True, exist_ok=True)
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.run()


def test_build_run_cleans_existing_artifacts_dir(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)

    artifacts_dir = plugin.get_dist_component_artifacts_dir("build")
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "stale.txt").write_text("old")

    with pytest.raises(BuildError, match="'sign-qube' option not configured"):
        plugin.run()

    assert not (artifacts_dir / "stale.txt").exists()


def test_build_run_debug_verbose_and_dummy_target_path(
    config, component, win_dist, monkeypatch
):
    config.set("debug", True)
    config.set("verbose", True)
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)
    monkeypatch.setattr(
        plugin,
        "get_parameters",
        lambda stage: {
            "build": [PackagePath("dummy"), PackagePath("component.sln")],
            "bin": [],
            "lib": [],
            "inc": [],
        },
    )
    monkeypatch.setattr(
        plugin,
        "get_config_stage_options",
        lambda stage: {"sign-qube": "sign-vm", "test-sign": False},
    )

    captured_cmds = []

    def _fake_run(cmds, *args, **kwargs):
        captured_cmds.append(cmds)
        return ""

    monkeypatch.setattr(plugin.executor, "run", _fake_run)
    monkeypatch.setattr(plugin.executor, "start_dispvm", lambda: "dvm-test")
    monkeypatch.setattr(plugin.executor, "kill_vm", lambda vm: None)
    monkeypatch.setattr(plugin, "sign_prep", lambda *args, **kwargs: b"crt")
    monkeypatch.setattr(plugin, "sign_sign", lambda *args, **kwargs: b"signed")
    monkeypatch.setattr(
        "qubesbuilder.plugins.build_windows.qrexec_call",
        lambda **kwargs: b"timestamped-bytes",
    )

    plugin.run()

    # First target is dummy: only copy-out prep commands are expected.
    assert captured_cmds[0]
    assert all("build-sln.ps1" not in cmd for cmd in captured_cmds[0])
    # Second target is .sln: debug and verbose flags must be added.
    sln_cmd = next(cmd for cmd in captured_cmds[1] if "build-sln.ps1" in cmd)
    assert "-log" in sln_cmd
    assert "-noisy" in sln_cmd


def test_build_run_skips_non_signable_and_skip_test_sign(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)
    monkeypatch.setattr(
        plugin,
        "get_parameters",
        lambda stage: {
            "build": [PackagePath("component.sln")],
            "bin": ["component.exe", "component.dll", "component.txt"],
            "lib": [],
            "inc": [],
            "skip-test-sign": ["component.dll"],
        },
    )
    monkeypatch.setattr(
        plugin,
        "get_config_stage_options",
        lambda stage: {"sign-qube": "sign-vm", "test-sign": True},
    )

    def _fake_run(*args, **kwargs):
        artifacts_dir = plugin.get_dist_component_artifacts_dir("build")
        (artifacts_dir / "bin").mkdir(parents=True, exist_ok=True)
        for name in ("component.exe", "component.dll", "component.txt"):
            (artifacts_dir / "bin" / name).write_bytes(b"MZ")
        return ""

    monkeypatch.setattr(plugin.executor, "run", _fake_run)
    monkeypatch.setattr(plugin.executor, "start_dispvm", lambda: "dvm-test")
    monkeypatch.setattr(plugin.executor, "kill_vm", lambda vm: None)
    monkeypatch.setattr(plugin, "sign_prep", lambda *args, **kwargs: b"crt")
    signed_files = []
    monkeypatch.setattr(
        plugin,
        "sign_sign",
        lambda qube, key_name, file: signed_files.append(file.name)
        or b"signed",
    )
    monkeypatch.setattr(plugin, "sign_delete_key", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "qubesbuilder.plugins.build_windows.qrexec_call",
        lambda **kwargs: b"timestamped-bytes",
    )

    plugin.run()
    assert signed_files == ["component.exe"]


def test_build_run_rejects_non_sln_target(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)
    monkeypatch.setattr(
        plugin,
        "get_parameters",
        lambda stage: {"build": [PackagePath("not-a-solution.txt")]},
    )
    monkeypatch.setattr(
        plugin,
        "get_config_stage_options",
        lambda stage: {"sign-qube": "sign-vm", "test-sign": False},
    )

    with pytest.raises(BuildError, match="can only build Visual Studio .sln"):
        plugin.run()


def test_build_run_detects_msbuild_failure(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor(threads=2)
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)
    monkeypatch.setattr(
        plugin,
        "get_config_stage_options",
        lambda stage: {"sign-qube": "sign-vm", "test-sign": False},
    )
    monkeypatch.setattr(
        plugin.executor, "run", lambda *args, **kwargs: "Build FAILED"
    )

    with pytest.raises(BuildError, match="Build failed, see msbuild output"):
        plugin.run()


def test_build_run_wraps_executor_error(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor()
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)
    monkeypatch.setattr(
        plugin,
        "get_config_stage_options",
        lambda stage: {"sign-qube": "sign-vm", "test-sign": False},
    )

    def _raise_executor_error(*args, **kwargs):
        raise ExecutorError("executor-failed")

    monkeypatch.setattr(plugin.executor, "run", _raise_executor_error)

    with pytest.raises(BuildError, match="Failed to build solution"):
        plugin.run()


def test_build_run_success_with_signing_and_timestamping(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsBuildPlugin(
        component=component, dist=win_dist, config=config, stage="build"
    )
    plugin.executor = DummyWindowsExecutor(threads=2)
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)
    monkeypatch.setattr(
        plugin,
        "get_config_stage_options",
        lambda stage: {"sign-qube": "sign-vm", "test-sign": True},
    )

    artifacts_dir = plugin.get_dist_component_artifacts_dir("build")
    killed = []
    deleted = []

    def _fake_run(*args, **kwargs):
        (artifacts_dir / "bin").mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "lib").mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "bin" / "component.exe").write_bytes(b"MZ")
        (artifacts_dir / "bin" / "component.dll").write_bytes(b"MZ")
        (artifacts_dir / "lib" / "component.lib").write_bytes(b"lib")
        return ""

    monkeypatch.setattr(plugin.executor, "run", _fake_run)
    monkeypatch.setattr(plugin.executor, "start_dispvm", lambda: "dvm-test")
    monkeypatch.setattr(
        plugin.executor, "kill_vm", lambda vm: killed.append(vm)
    )
    monkeypatch.setattr(
        plugin,
        "sign_prep",
        lambda qube, key_name, test_sign: b"certificate-bytes",
    )
    monkeypatch.setattr(
        plugin,
        "sign_sign",
        lambda qube, key_name, file: b"signed-bytes",
    )
    monkeypatch.setattr(
        plugin,
        "sign_delete_key",
        lambda qube, key_name: deleted.append((qube, key_name)),
    )
    monkeypatch.setattr(
        "qubesbuilder.plugins.build_windows.qrexec_call",
        lambda **kwargs: b"timestamped-bytes",
    )

    plugin.run()

    build_info = yaml.safe_load(
        (
            config.artifacts_dir
            / "components"
            / component.name
            / component.get_version_release()
            / win_dist.distribution
            / "build"
            / f"{BUILD_MANGLE}.build.yml"
        ).read_text()
    )
    assert "component.exe" in build_info["files"]["bin"]
    assert killed == ["dvm-test"]
    assert deleted == [("sign-vm", "Qubes Windows Tools")]


#
# sign stage
#


def test_sign_creates_artifact(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsSignPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="sign",
    )
    plugin.run()

    artifact = _sign_artifact_path(config, component, win_dist, BUILD_MANGLE)
    assert artifact.exists(), f"Expected sign artifact at {artifact}"
    info = yaml.safe_load(artifact.read_text()) or {}
    assert isinstance(info, dict)


def test_sign_artifact_is_idempotent(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsSignPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="sign",
    )
    plugin.run()
    artifact = _sign_artifact_path(config, component, win_dist, BUILD_MANGLE)
    artifact.write_text("toto: true\n")
    mtime_before = artifact.stat().st_mtime

    plugin.run()
    assert (
        artifact.stat().st_mtime == mtime_before
    ), "Sign artifact should not be overwritten on a repeated run"
    assert (
        artifact.read_text() == "toto: true\n"
    ), "Sign artifact content should be preserved"


def test_sign_from_args_accepts_valid_inputs(config, component, win_dist):
    plugin = WindowsSignPlugin.from_args(
        component=component,
        dist=win_dist,
        config=config,
        stage="sign",
    )
    assert isinstance(plugin, WindowsSignPlugin)


@pytest.mark.parametrize(
    ("stage", "dist", "has_packages"),
    [
        ("publish", QubesDistribution("vm-win10"), True),
        ("sign", QubesDistribution("vm-bookworm"), True),
        ("sign", QubesDistribution("vm-win10"), False),
    ],
)
def test_sign_from_args_rejects_invalid_inputs(
    config, component, stage, dist, has_packages
):
    component.has_packages = has_packages
    assert (
        WindowsSignPlugin.from_args(
            component=component,
            dist=dist,
            config=config,
            stage=stage,
        )
        is None
    )


#
# publish stage
#


def test_publish_creates_artifact(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.run()

    artifact = _publish_artifact_path(config, component, win_dist, BUILD_MANGLE)
    assert artifact.exists(), f"Expected publish artifact at {artifact}"
    info = yaml.safe_load(artifact.read_text())
    assert len(info["repository-publish"]) == 1
    assert info["repository-publish"][0]["name"] == "current-testing"


def test_publish_creates_bin_hardlinks(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)

    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.run()

    build_exe = (
        config.artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / win_dist.distribution
        / "build"
        / "bin"
        / "component.exe"
    )
    published_exe = (
        _published_component_dir(config, component, win_dist, "current-testing")
        / "bin"
        / "component.exe"
    )

    assert published_exe.exists()
    assert build_exe.stat().st_ino == published_exe.stat().st_ino


def test_publish_calls_sign_digest_when_signing_is_configured(
    config, component, win_dist, monkeypatch
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    config.set("sign-key", {"windows": "windows-key"})

    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    called = {}

    def _fake_sign_digest(component_dir, sign_key):
        called["component_dir"] = component_dir
        called["sign_key"] = sign_key

    monkeypatch.setattr(plugin, "sign_digest", _fake_sign_digest)

    plugin.run()

    assert called["sign_key"] == "windows-key"
    assert (
        called["component_dir"].name == f"{component.name}_{component.version}"
    )


def test_publish_skips_signing_without_gpg_client(
    config, component, win_dist, monkeypatch
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    config.set("sign-key", {"windows": "windows-key"})
    config.set("gpg-client", "")

    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    monkeypatch.setattr(
        plugin,
        "sign_digest",
        lambda *args, **kwargs: pytest.fail("sign_digest should not be called"),
    )

    plugin.run()


def test_publish_artifact_is_idempotent(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.run()
    artifact = _publish_artifact_path(config, component, win_dist, BUILD_MANGLE)

    custom = {
        "repository-publish": [
            {"name": "current-testing", "timestamp": "202601010000"}
        ]
    }
    artifact.write_text(yaml.safe_dump(custom))
    mtime_before = artifact.stat().st_mtime

    plugin.run()
    assert (
        artifact.stat().st_mtime == mtime_before
    ), "Publish artifact should not be overwritten on a repeated run"
    info = yaml.safe_load(artifact.read_text())
    assert info == custom, "Publish artifact content should be preserved"


def test_publish_unpublish_removes_repository_and_artifact(
    config, component, win_dist
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    plugin.run()
    artifact = _publish_artifact_path(config, component, win_dist, BUILD_MANGLE)
    component_dir = _published_component_dir(
        config, component, win_dist, "current-testing"
    )
    assert artifact.exists()
    assert component_dir.exists()

    plugin.run(unpublish=True)

    assert not artifact.exists()
    assert not component_dir.exists()


def test_publish_source_hash_change_unpublishes_old_repositories(
    config, component, win_dist, monkeypatch
):
    _write_build_artifact(
        config,
        component,
        win_dist,
        BUILD_MANGLE,
        source_hash="new-source-hash",
    )

    old_publish_info = {
        "source-hash": "old-source-hash",
        "repository-publish": [
            {"name": "current-testing", "timestamp": "202601010000"},
            {"name": "security-testing", "timestamp": "202601010000"},
        ],
    }
    _publish_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    ).parent.mkdir(parents=True, exist_ok=True)
    _publish_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    ).write_text(yaml.safe_dump(old_publish_info))

    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    unpublished_repositories = []

    def _fake_unpublish(build, repository_publish):
        unpublished_repositories.append(repository_publish)

    monkeypatch.setattr(plugin, "unpublish", _fake_unpublish)

    plugin.run(repository_publish="unstable")

    assert unpublished_repositories == ["current-testing", "security-testing"]
    info = yaml.safe_load(
        _publish_artifact_path(
            config, component, win_dist, BUILD_MANGLE
        ).read_text()
    )
    assert info["source-hash"] == "new-source-hash"
    assert [entry["name"] for entry in info["repository-publish"]] == [
        "unstable"
    ]


def test_publish_rejects_unknown_repository(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)

    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    with pytest.raises(PublishError, match="Refusing to publish components"):
        plugin.run(repository_publish="not-a-repository")


def test_publish_run_skips_when_no_repository(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    config.set("repository-publish", {})
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    # No repository configured: publish should be silently skipped, not raise.
    plugin.run()


def test_publish_run_refuses_current_when_not_old_enough(
    config, component, win_dist
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    with pytest.raises(PublishError, match="Refusing to publish to 'current'"):
        plugin.run(repository_publish="current")


def test_publish_run_missing_build_info_raises_publish_error(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    monkeypatch.setattr(plugin, "check_dependencies", lambda: None)

    with pytest.raises(PublishError, match="Cannot find build info"):
        plugin.run(repository_publish="unstable")


def test_publish_run_with_same_source_hash_reuses_publish_info(
    config, component, win_dist
):
    _write_build_artifact(
        config,
        component,
        win_dist,
        BUILD_MANGLE,
        source_hash="same-source-hash",
    )
    existing = {
        "source-hash": "same-source-hash",
        "repository-publish": [
            {"name": "current-testing", "timestamp": "202601010000"}
        ],
    }
    publish_artifact = _publish_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    )
    publish_artifact.parent.mkdir(parents=True, exist_ok=True)
    publish_artifact.write_text(yaml.safe_dump(existing))

    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.run(repository_publish="unstable")

    info = yaml.safe_load(publish_artifact.read_text())
    assert [entry["name"] for entry in info["repository-publish"]] == [
        "current-testing",
        "unstable",
    ]


def test_publish_run_unpublish_not_published(config, component, win_dist):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    plugin.run(unpublish=True, repository_publish="unstable")
    assert not _publish_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    ).exists()


def test_publish_run_unpublish_preserves_other_repositories(
    config, component, win_dist
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )

    plugin.run(repository_publish="current-testing")
    plugin.run(repository_publish="unstable")
    plugin.run(unpublish=True, repository_publish="unstable")

    info = yaml.safe_load(
        _publish_artifact_path(
            config, component, win_dist, BUILD_MANGLE
        ).read_text()
    )
    assert [entry["name"] for entry in info["repository-publish"]] == [
        "current-testing"
    ]


def test_publish_run_returns_when_component_has_no_packages(
    config, component, win_dist
):
    component.has_packages = False
    (config.sources_dir / component.name).mkdir(parents=True, exist_ok=True)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.run()


def test_publish_method_skips_when_no_files(config, component, win_dist):
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.publish(
        build=PackagePath(BUILD_MANGLE), repository_publish="unstable"
    )


def test_publish_method_raises_on_link_error(
    config, component, win_dist, monkeypatch
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    monkeypatch.setattr(
        "qubesbuilder.plugins.publish_windows.os.link",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("denied")
        ),
    )

    with pytest.raises(PublishError, match="Failed to publish artifacts"):
        plugin.publish(
            build=PackagePath(BUILD_MANGLE), repository_publish="unstable"
        )


def test_unpublish_method_skips_when_no_files(config, component, win_dist):
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    plugin.unpublish(
        build=PackagePath(BUILD_MANGLE), repository_publish="unstable"
    )


def test_unpublish_method_raises_on_rmtree_error(
    config, component, win_dist, monkeypatch
):
    _write_build_artifact(config, component, win_dist, BUILD_MANGLE)
    target_dir = _published_component_dir(
        config, component, win_dist, "unstable"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    monkeypatch.setattr(
        "qubesbuilder.plugins.publish_windows.shutil.rmtree",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("denied")
        ),
    )

    with pytest.raises(PublishError, match="Failed to unpublish artifacts"):
        plugin.unpublish(
            build=PackagePath(BUILD_MANGLE), repository_publish="unstable"
        )


def test_sign_digest_writes_checksums_and_invokes_executor(
    config, component, win_dist, tmp_path, monkeypatch
):
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    component_dir = tmp_path / "component"
    (component_dir / "bin").mkdir(parents=True, exist_ok=True)
    (component_dir / "bin" / "component.exe").write_bytes(b"MZ")
    (component_dir / "skip.asc").write_text("sig")

    executor = DummyPublishExecutor()
    monkeypatch.setattr(
        config,
        "get_executor_from_config",
        lambda stage, plugin=None: executor,
    )
    config.set("gpg-client", "gpg")

    plugin.sign_digest(component_dir=component_dir, sign_key="windows-key")

    content = (component_dir / "SHA256SUMS").read_text()
    assert "bin/component.exe" in content
    assert "skip.asc" not in content
    assert executor.commands
    assert "-u windows-key" in executor.commands[0][0]


def test_sign_digest_wraps_executor_error(
    config, component, win_dist, monkeypatch
):
    plugin = WindowsPublishPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="publish",
    )
    component_dir = config.artifacts_dir / "digest-fail"
    component_dir.mkdir(parents=True, exist_ok=True)
    (component_dir / "a.bin").write_bytes(b"abc")

    monkeypatch.setattr(
        config,
        "get_executor_from_config",
        lambda stage, plugin=None: DummyPublishExecutor(should_raise=True),
    )

    with pytest.raises(PublishError, match="Failed to sign digest"):
        plugin.sign_digest(component_dir=component_dir, sign_key="windows-key")


#
# all stages
#


def test_pipeline_all_stages_produce_artifacts(config, component, win_dist):
    """
    init-cache -> prep -> build (mimicked) -> sign -> publish.
    """
    # init-cache
    WindowsChrootPlugin(dist=win_dist, config=config, stage="init-cache").run()
    assert _init_cache_artifact_path(config, win_dist).exists()

    # prep
    _write_fetch_artifact(config, component)
    (config.sources_dir / component.name).mkdir(parents=True, exist_ok=True)
    WindowsSourcePlugin(
        component=component, dist=win_dist, config=config, stage="prep"
    ).run()
    assert _source_artifact_path(config, component, win_dist).exists()

    # build (mimicked - no Windows VM required)
    build_artifact = _write_build_artifact(
        config, component, win_dist, BUILD_MANGLE
    )
    assert build_artifact.exists()
    build_info = yaml.safe_load(build_artifact.read_text())
    assert "component.exe" in build_info["files"]["bin"]

    # sign
    WindowsSignPlugin(
        component=component, dist=win_dist, config=config, stage="sign"
    ).run()
    sign_artifact = _sign_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    )
    assert sign_artifact.exists()

    # publish
    WindowsPublishPlugin(
        component=component, dist=win_dist, config=config, stage="publish"
    ).run()
    publish_artifact = _publish_artifact_path(
        config, component, win_dist, BUILD_MANGLE
    )
    assert publish_artifact.exists()
    publish_info = yaml.safe_load(publish_artifact.read_text())
    assert len(publish_info["repository-publish"]) == 1
    assert publish_info["repository-publish"][0]["name"] == "current-testing"
