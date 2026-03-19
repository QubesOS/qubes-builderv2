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

import pytest
import yaml
from pathlib import Path

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.plugins.chroot_windows import WindowsChrootPlugin
from qubesbuilder.plugins.sign_windows import WindowsSignPlugin
from qubesbuilder.plugins.publish_windows import WindowsPublishPlugin


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
    # Initialize a git repo so get_source_commit_hash() succeeds
    for cmd in [
        ["git", "init", str(src)],
        ["git", "-C", str(src), "config", "user.email", "test@test.com"],
        ["git", "-C", str(src), "config", "user.name", "Test"],
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


def _write_build_artifact(config, component, dist, build_mangle):
    """
    Simulate a completed Windows build by writing a build artifact YAML that
    mirrors what WindowsBuildPlugin.run() would produce: binary (exe, dll)
    and library outputs, plus a source hash.  Also creates dummy binary files
    so that publish_windows can hardlink them.
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

    # Satisfy the fetch-stage dependency check
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
        "source-hash": "deadbeefdeadbeef",
    }
    artifact_path = build_dir / f"{build_mangle}.build.yml"
    artifact_path.write_text(yaml.safe_dump(info))
    return artifact_path


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


def _init_cache_artifact_path(config, dist):
    return (
        config.cache_dir
        / "chroot"
        / dist.distribution
        / dist.nva
        / f"{dist.nva}.init-cache.yml"
    )


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
    # Overwrite with toto content so we can detect a rewrite
    artifact.write_text("toto")

    plugin.run(force=True)
    info = yaml.safe_load(artifact.read_text())
    assert info == {
        "packages": []
    }, "Artifact should be regenerated with correct content after --force"


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
# sign stage
#


def test_sign_creates_artifact(config, component, win_dist):
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
    plugin = WindowsSignPlugin(
        component=component,
        dist=win_dist,
        config=config,
        stage="sign",
    )
    plugin.run()
    artifact = _sign_artifact_path(config, component, win_dist, BUILD_MANGLE)
    # Overwrite with toto so we can detect an unwanted rewrite
    artifact.write_text("toto: true\n")
    mtime_before = artifact.stat().st_mtime

    plugin.run()
    assert (
        artifact.stat().st_mtime == mtime_before
    ), "Sign artifact should not be overwritten on a repeated run"
    assert (
        artifact.read_text() == "toto: true\n"
    ), "Sign artifact content should be preserved"


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
    # Overwrite with a non-empty publish record to detect unwanted rewrite
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


#
# all stages
#


def test_pipeline_all_stages_produce_artifacts(config, component, win_dist):
    """
    init-cache -> build (mimicked) -> sign -> publish.
    """
    # init-cache
    WindowsChrootPlugin(dist=win_dist, config=config, stage="init-cache").run()
    assert _init_cache_artifact_path(config, win_dist).exists()

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
