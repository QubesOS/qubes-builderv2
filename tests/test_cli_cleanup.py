import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

import pytest
import yaml

from qubesbuilder.common import PROJECT_PATH

DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder-ci.yml"
HASH_RE = re.compile(r"[a-f0-9]{40}")


class RandomData:
    COMPONENTS = [
        "core-qrexec",
        "core-vchan-xen",
    ]
    VERSIONS = ["1.0", "1.1", "1.2", "1.3"]
    DISTFILES = [f"distfile{i}.tar.gz" for i in range(1, 3)]
    LOG_FILES = 10
    TMP_FILES = 10

    def __init__(self, artifacts_dir):
        self.artifacts_dir = artifacts_dir
        self.components_dir = artifacts_dir / "components"
        self.distfiles_dir = artifacts_dir / "distfiles"
        self.logs_dir = artifacts_dir / "logs"
        self.tmp_dir = artifacts_dir / "cache"
        self.sources_dir = artifacts_dir / "sources"

    def create_components(self):
        for component in self.COMPONENTS:
            component_path = self.components_dir / component
            component_path.mkdir(parents=True, exist_ok=True)
            for version in self.VERSIONS:
                version_path = component_path / version
                version_path.mkdir(parents=True, exist_ok=True)
                for file_index in range(10):
                    (version_path / f"file{file_index}.txt").write_text(
                        "dummy content"
                    )

    def create_logs(self):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        for i in range(self.LOG_FILES):
            log_file = self.logs_dir / f"log_{i}.log"
            log_file.write_text(f"log content {i}")
            log_time = now - timedelta(days=i)
            os.utime(log_file, (log_time.timestamp(), log_time.timestamp()))

    def create_cache_dirs(self):
        (self.tmp_dir / "chroot").mkdir(parents=True, exist_ok=True)
        installer_dir = self.tmp_dir / "installer"
        installer_dir.mkdir(parents=True, exist_ok=True)

        for i in range(10):
            qubes_dir = installer_dir / f"Qubes-R{i}"
            qubes_dir.mkdir(parents=True, exist_ok=True)
            for subdir in ["rpm", "work"]:
                subdir_path = qubes_dir / subdir
                subdir_path.mkdir(parents=True, exist_ok=True)
                for j in range(5):
                    (subdir_path / f"file{j}.tmp").write_text(
                        f"cache content {j}"
                    )

        template_dir = installer_dir / "template"
        template_dir.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            (template_dir / f"template{i}.tmp").write_text(
                f"template cache content {i}"
            )

    def run(self):
        self.create_components()
        self.create_logs()
        self.create_cache_dirs()

    def verify_structure_before_cleanup(self):
        # Check components
        for component in self.COMPONENTS:
            component_path = self.components_dir / component
            assert component_path.exists()
            for version in self.VERSIONS:
                version_path = component_path / version
                assert version_path.exists()
                for file_index in range(10):
                    assert (version_path / f"file{file_index}.txt").exists()

        # Check logs
        for i in range(self.LOG_FILES):
            assert (self.logs_dir / f"log_{i}.log").exists()

        # Check cache directories
        for i in range(10):
            qubes_dir = self.tmp_dir / "installer" / f"Qubes-R{i}"
            assert qubes_dir.exists()
            for subdir in ["rpm", "work"]:
                subdir_path = qubes_dir / subdir
                assert subdir_path.exists()
                for j in range(5):
                    assert (subdir_path / f"file{j}.tmp").exists()

        template_dir = self.tmp_dir / "installer" / "template"
        assert template_dir.exists()
        for i in range(10):
            assert (template_dir / f"template{i}.tmp").exists()

    def verify_structure_after_cleanup(self):
        # Check components - only the last 2 versions should remain
        for component in self.COMPONENTS:
            component_path = self.components_dir / component
            assert component_path.exists()
            versions = sorted(
                [v for v in component_path.iterdir()],
                reverse=True,
            )
            assert len(versions) == 2
            for version in versions:
                for file_index in range(10):
                    assert (version / f"file{file_index}.txt").exists()

        # Check logs - only the recent 5 days logs should remain
        now = datetime.now()
        cutoff_date = now - timedelta(days=5)
        remaining_logs = list(self.logs_dir.iterdir())
        for log_file in remaining_logs:
            log_time = datetime.fromtimestamp(log_file.stat().st_mtime)
            assert log_time >= cutoff_date

        # Check cache directories - ensure cache directories are removed correctly
        assert not (self.tmp_dir / "chroot").exists()
        installer_dir = self.tmp_dir / "installer"
        for i in range(10):
            qubes_dir = installer_dir / f"Qubes-R{i}"
            assert not qubes_dir.exists()
        assert not (installer_dir / "template").exists()
        assert not (self.tmp_dir / "other_cache_dirs").exists()


@pytest.fixture
def artifacts_dir():
    if os.environ.get("BASE_ARTIFACTS_DIR"):
        tmpdir = tempfile.mktemp(
            prefix="github-", dir=os.environ.get("BASE_ARTIFACTS_DIR")
        )
    else:
        tmpdir = tempfile.mktemp(prefix="github-")
    artifacts_dir = pathlib.Path(tmpdir) / "artifacts"
    if not artifacts_dir.exists():
        artifacts_dir.mkdir(parents=True)
    yield artifacts_dir
    shutil.rmtree(tmpdir)


def qb_call(builder_conf, artifacts_dir, *args, **kwargs):
    cmd = [
        "python3",
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={artifacts_dir}",
        *args,
    ]
    subprocess.check_call(cmd, **kwargs)


def qb_call_output(builder_conf, artifacts_dir, *args, **kwargs):
    cmd = [
        "python3",
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={artifacts_dir}",
        *args,
    ]
    return subprocess.check_output(cmd, **kwargs)


def test_cleanup_distfiles(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "linux-gbulb",
        "package",
        "fetch",
    )

    distfiles_dir = artifacts_dir / "distfiles" / "linux-gbulb"
    distfiles_dir.mkdir(parents=True, exist_ok=True)
    dummy_file = distfiles_dir / "dummy.txt"
    dummy_file.write_text("dummy content")
    dummy_file2 = distfiles_dir / "dummy2.txt"
    dummy_file2.write_text("dummy content 2")
    current_distfile = distfiles_dir / "gbulb-0.6.3.tar.gz"

    qb_call(DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "distfiles")

    assert not dummy_file.exists()
    assert not dummy_file2.exists()
    assert current_distfile.exists()


def test_cleanup_build_artifacts(artifacts_dir):
    components_dir = artifacts_dir / "components" / "linux-gbulb"
    components_dir.mkdir(parents=True, exist_ok=True)
    for version in ["0.1.2-3", "0.4.5-6", "0.7.8-9", "1.0.0-1"]:
        old_version = components_dir / version
        old_version.mkdir()
        old_version_file = old_version / "file.txt"
        old_version_file.write_text("some content")

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "build-artifacts",
        "--keep-versions",
        "2",
    )

    assert not (components_dir / "0.1.2-3").exists()
    assert not (components_dir / "0.4.5-6").exists()
    assert (components_dir / "1.0.0-1").exists() and (
        components_dir / "1.0.0-1"
    ).is_dir()
    assert (components_dir / "0.7.8-9").exists() and (
        components_dir / "0.7.8-9"
    ).is_dir()


def test_cleanup_build_artifacts_sequence(artifacts_dir):
    components_dir = artifacts_dir / "components" / "linux-gbulb"
    components_dir.mkdir(parents=True, exist_ok=True)
    for version in ["1.7", "1.8", "1.9", "1.10"]:
        old_version = components_dir / version
        old_version.mkdir()
        old_version_file = old_version / "file.txt"
        old_version_file.write_text("some content")

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "build-artifacts",
        "--keep-versions",
        "1",
    )

    for version in ["1.7", "1.8", "1.9"]:
        assert not (components_dir / version).exists()

    assert (components_dir / "1.10").exists() and (
        components_dir / "1.10"
    ).is_dir()


def test_cleanup_logs(artifacts_dir):
    logs_dir = artifacts_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Create 20 old log files
    for i in range(20):
        log_file = logs_dir / f"old_log_{i}.log"
        log_file.write_text(f"old log {i}")
        old_log_time = datetime.now() - timedelta(days=31 + i)
        os.utime(log_file, (old_log_time.timestamp(), old_log_time.timestamp()))

    # Create 5 recent log files
    for i in range(5):
        log_file = logs_dir / f"recent_log_{i}.log"
        log_file.write_text(f"recent log {i}")

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "logs",
        "--log-retention-days",
        "30",
    )

    # Check only the 5 recent logs remain
    remaining_logs = list(logs_dir.iterdir())
    assert len(remaining_logs) == 5
    for log_file in remaining_logs:
        assert "recent_log_" in log_file.name


def test_cleanup_tmp(artifacts_dir):
    tmp_dir = artifacts_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = tmp_dir / "empty_dir"
    empty_dir.mkdir()
    non_empty_dir = tmp_dir / "non_empty_dir"
    non_empty_dir.mkdir()
    dummy_temp_file = non_empty_dir / "temp.txt"
    dummy_temp_file.write_text("temporary content")

    qb_call(DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "tmp")

    assert not empty_dir.exists()
    assert non_empty_dir.exists()
    assert dummy_temp_file.exists()


def test_cleanup_tmp_force(artifacts_dir):
    tmp_dir = artifacts_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = tmp_dir / "empty_dir"
    empty_dir.mkdir()
    non_empty_dir = tmp_dir / "non_empty_dir"
    non_empty_dir.mkdir()
    dummy_temp_file = non_empty_dir / "temp.txt"
    dummy_temp_file.write_text("temporary content")

    qb_call(DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "tmp", "--force")

    assert not empty_dir.exists()
    assert not non_empty_dir.exists()
    assert not dummy_temp_file.exists()


def test_cleanup_cache(artifacts_dir):
    cache_dir = artifacts_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    chroot_cache_dir = cache_dir / "chroot"
    chroot_cache_dir.mkdir(parents=True, exist_ok=True)

    installer_cache_dir = cache_dir / "installer"
    installer_cache_dir.mkdir(parents=True, exist_ok=True)

    installer_chroot_dir = installer_cache_dir / "chroot" / "mock"
    installer_chroot_dir.mkdir(parents=True, exist_ok=True)

    installer_templates_dir = installer_cache_dir / "templates"
    installer_templates_dir.mkdir(parents=True, exist_ok=True)

    chroot_file = chroot_cache_dir / "file.txt"
    chroot_file.write_text("chroot content")

    installer_chroot_file = installer_chroot_dir / "file.txt"
    installer_chroot_file.write_text("installer chroot content")

    installer_templates_file = installer_templates_dir / "file.txt"
    installer_templates_file.write_text("installer templates content")

    # Test chroot cache cleanup
    qb_call(DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "cache", "--chroot")
    assert not chroot_cache_dir.exists()
    assert installer_cache_dir.exists()
    assert installer_chroot_dir.exists()
    assert installer_templates_dir.exists()

    # Recreate chroot cache for next test
    chroot_cache_dir.mkdir(parents=True, exist_ok=True)
    chroot_file.write_text("chroot content")

    # Test installer chroot cache cleanup
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "cache",
        "--installer-chroot",
    )
    assert chroot_cache_dir.exists()
    assert installer_cache_dir.exists()
    assert not installer_chroot_dir.exists()
    assert installer_templates_dir.exists()

    # Recreate installer chroot cache for next test
    installer_chroot_dir.mkdir(parents=True, exist_ok=True)
    installer_chroot_file.write_text("installer chroot content")

    # Test installer templates cache cleanup
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "cache",
        "--installer-templates",
    )
    assert chroot_cache_dir.exists()
    assert installer_cache_dir.exists()
    assert installer_chroot_dir.exists()
    assert not installer_templates_dir.exists()

    # Test full installer cache cleanup
    qb_call(
        DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "cache", "--installer"
    )
    assert chroot_cache_dir.exists()
    assert not installer_chroot_dir.exists()
    assert not installer_templates_dir.exists()

    # Test all cache cleanup
    qb_call(DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "cache", "--all")
    assert not chroot_cache_dir.exists()
    assert not installer_cache_dir.exists()


def test_cleanup_chroot_only_unused(artifacts_dir):
    cache_dir = artifacts_dir / "cache"
    chroot_dir = cache_dir / "chroot"
    chroot_dir.mkdir(parents=True, exist_ok=True)

    used_distros = ["fc37", "bookworm"]
    unused_distro = "fc32"

    for distro in used_distros:
        (chroot_dir / distro).mkdir(parents=True, exist_ok=True)
    (chroot_dir / unused_distro).mkdir(parents=True, exist_ok=True)

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "cache",
        "--chroot-only-unused",
    )

    # Check that the unused chroot directory was removed
    assert not (chroot_dir / unused_distro).exists()
    # Check that the used chroot directories remain
    for distro in used_distros:
        assert (chroot_dir / distro).exists()


def test_cleanup_distfiles_dry_run(artifacts_dir):
    distfiles_dir = artifacts_dir / "distfiles" / "linux-gbulb"
    distfiles_dir.mkdir(parents=True, exist_ok=True)
    dummy_file = distfiles_dir / "dummy.txt"
    dummy_file.write_text("dummy content")

    output = qb_call_output(
        DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "--dry-run", "distfiles"
    )
    assert dummy_file.exists()
    assert f"DRY-RUN: {dummy_file}" in output.decode()


def test_cleanup_build_artifacts_dry_run(artifacts_dir):
    components_dir = artifacts_dir / "components" / "linux-gbulb"
    components_dir.mkdir(parents=True, exist_ok=True)
    old_version = components_dir / "1.0.0"
    old_version.mkdir()

    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "--dry-run",
        "build-artifacts",
        "--keep-versions",
        "0",
    )
    assert old_version.exists()
    assert f"DRY-RUN: {old_version}" in output.decode()


def test_cleanup_logs_dry_run(artifacts_dir):
    logs_dir = artifacts_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    old_log = logs_dir / "old.log"
    old_log.write_text("old log")
    old_log_time = datetime.now() - timedelta(days=31)
    os.utime(old_log, (old_log_time.timestamp(), old_log_time.timestamp()))

    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "--dry-run",
        "logs",
        "--log-retention-days",
        "30",
    )
    assert old_log.exists()
    assert f"DRY-RUN: {old_log}" in output.decode()


def test_cleanup_tmp_dry_run(artifacts_dir):
    tmp_dir = artifacts_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = tmp_dir / "empty_dir"
    empty_dir.mkdir()
    non_empty_dir = tmp_dir / "non_empty_dir"
    non_empty_dir.mkdir()
    dummy_temp_file = non_empty_dir / "temp.txt"
    dummy_temp_file.write_text("temporary content")

    output = qb_call_output(
        DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "--dry-run", "tmp"
    )
    assert empty_dir.exists()
    assert non_empty_dir.exists()
    assert dummy_temp_file.exists()
    assert f"DRY-RUN: {empty_dir}" in output.decode()
    assert f"DRY-RUN: {non_empty_dir}" not in output.decode()


def test_cleanup_tmp_force_dry_run(artifacts_dir):
    tmp_dir = artifacts_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = tmp_dir / "empty_dir"
    empty_dir.mkdir()
    non_empty_dir = tmp_dir / "non_empty_dir"
    non_empty_dir.mkdir()
    dummy_temp_file = non_empty_dir / "temp.txt"
    dummy_temp_file.write_text("temporary content")

    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "--dry-run",
        "tmp",
        "--force",
    )
    assert empty_dir.exists()
    assert non_empty_dir.exists()
    assert dummy_temp_file.exists()
    assert f"DRY-RUN: {empty_dir}" in output.decode()
    assert f"DRY-RUN: {non_empty_dir}" in output.decode()


def test_cleanup(artifacts_dir):
    # Generate initial test data
    random_data = RandomData(artifacts_dir)
    random_data.run()

    # Verify initial structure
    random_data.verify_structure_before_cleanup()

    # Run cleanup
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "build-artifacts",
        "--keep-versions",
        "2",
    )
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "cleanup",
        "logs",
        "--log-retention-days",
        "5",
    )
    qb_call(DEFAULT_BUILDER_CONF, artifacts_dir, "cleanup", "cache", "--all")

    # Verify structure after cleanup
    random_data.verify_structure_after_cleanup()
