import tempfile
from pathlib import Path

import pytest
import yaml

from qubesbuilder.cli.cli_repository import _check_release_status_for_component
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution


RPM_QUBESBUILDER = """\
vm:
  rpm:
    build:
      - component.spec
"""

WIN_QUBESBUILDER = """\
vm:
  windows:
    build:
      - component.sln
"""

DEB_QUBESBUILDER = """\
vm:
  deb:
    build:
      - debian/
"""

MIXED_QUBESBUILDER = """\
vm:
  rpm:
    build:
      - component.spec
  windows:
    build:
      - component.sln
"""

RPM_BUILD_MANGLE = "component.spec"
WIN_BUILD_MANGLE = "component.sln"


def _write_fetch_artifact(
    artifacts_dir: Path, component: QubesComponent, vtags=None
):
    """Create the fetch stage artifact YAML (source.fetch.yml)."""
    fetch_dir = (
        artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / "nodist"
        / "fetch"
    )
    fetch_dir.mkdir(parents=True, exist_ok=True)
    info = {
        "git-commit-hash": "tititototata",
        "git-version-tags": vtags if vtags is not None else ["v1.0"],
    }
    (fetch_dir / "source.fetch.yml").write_text(yaml.dump(info))


def _write_build_artifact(
    artifacts_dir: Path,
    component: QubesComponent,
    dist: QubesDistribution,
    build_mangle: str,
):
    """Create a build stage artifact YAML ({mangle}.build.yml)."""
    build_dir = (
        artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "build"
    )
    build_dir.mkdir(parents=True, exist_ok=True)
    info = {"files": []}
    (build_dir / f"{build_mangle}.build.yml").write_text(yaml.dump(info))


def _write_publish_artifact(
    artifacts_dir: Path,
    component: QubesComponent,
    dist: QubesDistribution,
    build_mangle: str,
    repos=None,
):
    """Create a publish stage artifact YAML ({mangle}.publish.yml)."""
    publish_dir = (
        artifacts_dir
        / "components"
        / component.name
        / component.get_version_release()
        / dist.distribution
        / "publish"
    )
    publish_dir.mkdir(parents=True, exist_ok=True)
    if repos is None:
        repos = [{"name": "current-testing", "timestamp": "202601010000"}]
    info = {"repository-publish": repos}
    (publish_dir / f"{build_mangle}.publish.yml").write_text(yaml.dump(info))


@pytest.fixture
def workdir(tmp_path):
    src = tmp_path / "test-component"
    src.mkdir()
    (src / "version").write_text("1.0")
    (src / "rel").write_text("1")
    return tmp_path


@pytest.fixture
def config(workdir):
    artifacts_dir = workdir / "artifacts"
    artifacts_dir.mkdir()
    conf_file = workdir / "builder.yml"
    conf_file.write_text(
        f"executor:\n  type: local\nartifacts-dir: {artifacts_dir}\n"
        "repository-publish:\n  components: current-testing\n"
    )
    return Config(conf_file)


@pytest.fixture
def rpm_dist():
    return QubesDistribution("vm-fc42")


@pytest.fixture
def win_dist():
    return QubesDistribution("vm-win10")


def _make_component(workdir, qubesbuilder_content):
    src = workdir / "test-component"
    (src / ".qubesbuilder").write_text(qubesbuilder_content)
    return QubesComponent(src)


def _status(result, component, dist):
    return result[component.name][dist.distribution]


#
# Linux (RPM)
#


def test_rpm_no_source(workdir, config, rpm_dist):
    """
    Missing source directory -> 'no source'.
    """
    component = QubesComponent(workdir / "nonexistent-component")
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    assert _status(result, component, rpm_dist)["status"] == "no source"


def test_rpm_no_fetch_artifacts(workdir, config, rpm_dist):
    """
    Source exists but fetch has never run -> 'no fetch artifacts'.
    """
    component = _make_component(workdir, RPM_QUBESBUILDER)
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    assert (
        _status(result, component, rpm_dist)["status"] == "no fetch artifacts"
    )


def test_rpm_no_packages_defined(workdir, config, rpm_dist):
    """
    Component has no build targets for the requested distribution -> 'no packages defined'.
    """
    # Only debian targets defined, not RPM
    component = _make_component(workdir, DEB_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    assert (
        _status(result, component, rpm_dist)["status"] == "no packages defined"
    )


def test_rpm_not_released_with_version_tag(workdir, config, rpm_dist):
    """
    Fetch done, no publish artifact -> 'not released', version tag propagated.
    """
    component = _make_component(workdir, RPM_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component, vtags=["v1.0"])
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    s = _status(result, component, rpm_dist)
    assert s["status"] == "not released"
    assert s["tag"] == "v1.0"


def test_rpm_not_released_no_version_tag(workdir, config, rpm_dist):
    """
    Fetch done with no version tags -> 'not released', tag is 'no version tag'.
    """
    component = _make_component(workdir, RPM_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component, vtags=[])
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    s = _status(result, component, rpm_dist)
    assert s["status"] == "not released"
    assert s["tag"] == "no version tag"


def test_rpm_built_not_released(workdir, config, rpm_dist):
    """
    Publish artifact exists but not in any repo -> 'built, not released'.
    """
    component = _make_component(workdir, RPM_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    _write_build_artifact(
        config.artifacts_dir, component, rpm_dist, RPM_BUILD_MANGLE
    )
    _write_publish_artifact(
        config.artifacts_dir, component, rpm_dist, RPM_BUILD_MANGLE, repos=[]
    )
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    assert (
        _status(result, component, rpm_dist)["status"] == "built, not released"
    )


def test_rpm_released(workdir, config, rpm_dist):
    """
    Publish artifact present and published to current-testing -> 'released'.
    """
    component = _make_component(workdir, RPM_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    _write_publish_artifact(
        config.artifacts_dir, component, rpm_dist, RPM_BUILD_MANGLE
    )
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[rpm_dist]
    )
    s = _status(result, component, rpm_dist)
    assert s["status"] == "released"
    assert any(r["name"] == "current-testing" for r in s["repo"])


#
# Windows
#


def test_windows_no_source(workdir, config, win_dist):
    """
    Missing source directory -> 'no source'.
    """
    component = QubesComponent(workdir / "nonexistent-component")
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    assert _status(result, component, win_dist)["status"] == "no source"


def test_windows_no_fetch_artifacts(workdir, config, win_dist):
    """
    Source exists but fetch has never run -> 'no fetch artifacts'.
    """
    component = _make_component(workdir, WIN_QUBESBUILDER)
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    assert (
        _status(result, component, win_dist)["status"] == "no fetch artifacts"
    )


def test_windows_no_packages_defined(workdir, config, win_dist):
    """
    Component has no Windows build targets -> 'no packages defined'.
    """
    # Only RPM targets defined, not Windows
    component = _make_component(workdir, RPM_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    assert (
        _status(result, component, win_dist)["status"] == "no packages defined"
    )


def test_windows_not_released_with_version_tag(workdir, config, win_dist):
    """
    Fetch done, no publish artifact -> 'not released', version tag propagated.
    """
    component = _make_component(workdir, WIN_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component, vtags=["v1.0"])
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    s = _status(result, component, win_dist)
    assert s["status"] == "not released"
    assert s["tag"] == "v1.0"


def test_windows_not_released_no_version_tag(workdir, config, win_dist):
    """
    Fetch done with no version tags -> 'not released', tag is 'no version tag'.
    """
    component = _make_component(workdir, WIN_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component, vtags=[])
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    s = _status(result, component, win_dist)
    assert s["status"] == "not released"
    assert s["tag"] == "no version tag"


def test_windows_built_not_released(workdir, config, win_dist):
    """
    Publish artifact exists but not in any repo -> 'built, not released'.
    """
    component = _make_component(workdir, WIN_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    _write_build_artifact(
        config.artifacts_dir, component, win_dist, WIN_BUILD_MANGLE
    )
    _write_publish_artifact(
        config.artifacts_dir, component, win_dist, WIN_BUILD_MANGLE, repos=[]
    )
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    assert (
        _status(result, component, win_dist)["status"] == "built, not released"
    )


def test_windows_released(workdir, config, win_dist):
    """
    Publish artifact present and published to current-testing -> 'released'.
    """
    component = _make_component(workdir, WIN_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    _write_publish_artifact(
        config.artifacts_dir, component, win_dist, WIN_BUILD_MANGLE
    )
    result = _check_release_status_for_component(
        config=config, components=[component], distributions=[win_dist]
    )
    s = _status(result, component, win_dist)
    assert s["status"] == "released"
    assert any(r["name"] == "current-testing" for r in s["repo"])


#
# Multi distribution
#


def test_multiple_distributions(workdir, config, rpm_dist, win_dist):
    """
    A single component with both RPM and Windows targets is checked correctly
    for each distribution in one call.
    """
    component = _make_component(workdir, MIXED_QUBESBUILDER)
    _write_fetch_artifact(config.artifacts_dir, component)
    # RPM: not released (no publish artifact)
    # Windows: released (publish artifact present)
    _write_publish_artifact(
        config.artifacts_dir, component, win_dist, WIN_BUILD_MANGLE
    )

    result = _check_release_status_for_component(
        config=config,
        components=[component],
        distributions=[rpm_dist, win_dist],
    )

    assert (
        result[component.name][rpm_dist.distribution]["status"]
        == "not released"
    )
    assert result[component.name][win_dist.distribution]["status"] == "released"
