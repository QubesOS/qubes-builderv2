import hashlib
import os.path
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from qubesbuilder.common import PROJECT_PATH
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.plugins import Plugin
from qubesbuilder.plugins.publish import PublishError
from qubesbuilder.plugins.publish_archlinux import ArchlinuxRepoPlugin

DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder-ci.yml"
HASH_RE = re.compile(r"[a-f0-9]{40}")

releases = ["r4.2", "devel"]


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


def qb_call(builder_conf, artifacts_dir, release, *args, **kwargs):
    cmd = [
        "python3",
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={artifacts_dir}",
        "--option",
        f"qubes-release={release}",
        *args,
    ]
    subprocess.check_call(cmd, **kwargs)


def qb_call_output(builder_conf, artifacts_dir, release, *args, **kwargs):
    cmd = [
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={artifacts_dir}",
        "--option",
        f"qubes-release={release}",
        *args,
    ]
    return subprocess.check_output(cmd, **kwargs)


def _archlinux_repo_plugin(tmp_path):
    plugin = object.__new__(ArchlinuxRepoPlugin)
    plugin.config = SimpleNamespace(
        repository_publish_dir=tmp_path,
        qubes_release="r4.2",
        sign_key={"archlinux": "test-key"},
        gpg_client="gpg2",
        get_executor_from_config=Mock(),
    )
    plugin.dist = QubesDistribution("vm-archlinux")
    plugin.log = Mock()
    plugin.log_prefix = f"{plugin.name}:{plugin.dist}"
    return plugin


def test_repository_archlinux_metadata_with_packages(tmp_path):
    plugin = _archlinux_repo_plugin(tmp_path)
    repository_db = plugin.get_repository_db("current-testing")
    repository_db.parent.mkdir(parents=True)
    (repository_db.parent / "example.pkg.tar.zst").touch()
    (repository_db.parent / "example.pkg.tar.zst.sig").touch()
    repository_db_sig = repository_db.with_suffix(".gz.sig")
    repository_db_link_sig = repository_db.with_name(
        repository_db.name.removesuffix(".tar.gz") + ".sig"
    )
    repository_db_sig.touch()
    repository_db_link_sig.touch()
    executor = Mock()

    assert plugin.create_repository_metadata(executor, "current-testing") == repository_db
    command = executor.run.call_args.args[0][0]
    assert "repo-add" in command
    assert "! -name '*.sig'" in command
    assert not repository_db_sig.exists()
    assert not repository_db_link_sig.exists()


def test_repository_archlinux_metadata_wraps_executor_error(tmp_path):
    plugin = _archlinux_repo_plugin(tmp_path)
    repository_db = plugin.get_repository_db("current-testing")
    repository_db.parent.mkdir(parents=True)
    (repository_db.parent / "example.pkg.tar.zst").touch()
    executor = Mock()
    executor.run.side_effect = ExecutorError("repo-add failed")

    with pytest.raises(PublishError, match="Failed to create metadata"):
        plugin.create_repository_metadata(executor, "current-testing")


@pytest.mark.parametrize(
    ("sign_key", "gpg_client", "message"),
    [
        ({}, "gpg2", "No signing key found"),
        ({"archlinux": "test-key"}, None, "Please specify GPG client"),
    ],
)
def test_repository_archlinux_requires_signing_configuration(
    tmp_path, sign_key, gpg_client, message
):
    plugin = _archlinux_repo_plugin(tmp_path)
    plugin.config.sign_key = sign_key
    plugin.config.gpg_client = gpg_client
    plugin.create_repository_metadata = Mock()

    plugin.create_and_sign_repository_metadata("current-testing")

    plugin.create_repository_metadata.assert_not_called()
    assert message in plugin.log.info.call_args.args[0]


def test_repository_archlinux_create_requires_name(tmp_path):
    plugin = _archlinux_repo_plugin(tmp_path)
    plugin.create_and_sign_repository_metadata = Mock()

    plugin.create(None)

    plugin.create_and_sign_repository_metadata.assert_not_called()
    plugin.log.error.assert_called_once()


def test_repository_archlinux_run_delegates(tmp_path, monkeypatch):
    plugin = _archlinux_repo_plugin(tmp_path)
    delegated = []
    monkeypatch.setattr(Plugin, "run", lambda self: delegated.append(self))

    plugin.run()

    assert delegated == [plugin]


@pytest.mark.parametrize("release", releases)
def test_repository_create_vm_fc43(artifacts_dir, release):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            release,
            "-c",
            "qubes-release",
            "package",
            "fetch",
            env=env,
        )

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            release,
            "-c",
            "example-advanced",
            "-d",
            "vm-fc43",
            "repository",
            "create",
            "current",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/{release}/current/vm/fc43/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert f"/pub/os/qubes/repo/yum/{release}/current/vm/fc43/repodata/repomd.xml" in (
            metadata_dir / "repomd.xml.metalink"
        ).read_text(
            encoding="ascii"
        )


@pytest.mark.parametrize("release", releases)
def test_repository_create_vm_bookworm(artifacts_dir, release):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        for repo in ["current", "current-testing", "unstable"]:
            qb_call(
                DEFAULT_BUILDER_CONF,
                artifacts_dir,
                release,
                "-c",
                "example-advanced",
                "-d",
                "vm-bookworm",
                "repository",
                "create",
                repo,
                env=env,
            )

        repository_dir = artifacts_dir / f"repository-publish/deb/{release}/vm"
        for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
            assert (repository_dir / "dists" / codename / "InRelease").exists()
            assert (
                repository_dir / "dists" / codename / "Release.gpg"
            ).exists()


@pytest.mark.parametrize("release", releases)
def test_repository_create_vm_archlinux(artifacts_dir, release):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            release,
            "-d",
            "vm-archlinux",
            "repository",
            "create",
            "current-testing",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/archlinux/{release}/current-testing/vm/archlinux/pkgs"
        )
        repository_db = (
            metadata_dir / f"qubes-{release}-current-testing.db.tar.gz"
        )
        repository_files = (
            metadata_dir / f"qubes-{release}-current-testing.files.tar.gz"
        )
        repository_db_sig = repository_db.with_suffix(".gz.sig")
        repository_db_link_sig = repository_db.with_name(
            repository_db.name.removesuffix(".tar.gz") + ".sig"
        )
        assert repository_db.exists()
        assert repository_files.exists()
        assert repository_db_sig.exists()
        assert repository_db_link_sig.is_symlink()
        assert repository_db_link_sig.resolve() == repository_db_sig
        assert repository_db.with_name(
            repository_db.name.removesuffix(".tar.gz")
        ).is_symlink()
        assert repository_files.with_name(
            repository_files.name.removesuffix(".tar.gz")
        ).is_symlink()
        with tarfile.open(repository_db) as repository:
            assert repository.getnames() == []
        with tarfile.open(repository_files) as repository:
            assert repository.getnames() == []
        subprocess.run(
            ["gpg2", "-q", "--verify", repository_db_sig, repository_db],
            check=True,
            capture_output=True,
            env=env,
        )


@pytest.mark.parametrize("release", releases)
def test_repository_create_template(artifacts_dir, release):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            release,
            "-t",
            "whonix-gateway-18",
            "repository",
            "create",
            "templates-community-testing",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/{release}/templates-community-testing/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert f"/pub/os/qubes/repo/yum/{release}/templates-community-testing/repodata/repomd.xml" in (
            metadata_dir / "repomd.xml.metalink"
        ).read_text(
            encoding="ascii"
        )

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            release,
            "-t",
            "fedora-43-xfce",
            "repository",
            "create",
            "templates-itl-testing",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/{release}/templates-itl-testing/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert f"/pub/os/qubes/repo/yum/{release}/templates-itl-testing/repodata/repomd.xml" in (
            metadata_dir / "repomd.xml.metalink"
        ).read_text(
            encoding="ascii"
        )

        # ensure we don't have anything related to deb for template repository in clean artifacts dir
        assert not (artifacts_dir / "repository-publish/deb").exists()


@pytest.mark.parametrize("release", releases)
def test_repository_upload_template_does_not_rebuild(artifacts_dir, release):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        template_rpm = (
            "qubes-template-fedora-43-xfce-4.2.0-202601010000.noarch.rpm"
        )
        published_rpm_dir = (
            artifacts_dir
            / f"repository-publish/rpm/{release}/templates-itl-testing/rpm"
        )
        published_rpm_dir.mkdir(parents=True)
        (published_rpm_dir / template_rpm).write_bytes(b"placeholder\n")

        remote = pathlib.Path(tmpdir) / "remote"

        # No templates configured: upload must still push the published repo
        conf = yaml.safe_load(DEFAULT_BUILDER_CONF.read_text())
        conf["templates"] = []
        conf["executor"]["options"]["image"] = "does-not-exist-must-not-build"
        conf["repository-upload-remote-host"] = {"rpm": str(remote)}
        builder_conf = tmpdir + "/builder.yml"
        with open(builder_conf, "w") as builder_f:
            yaml.safe_dump(conf, builder_f)

        qb_call(
            builder_conf,
            artifacts_dir,
            release,
            "repository",
            "upload",
            "templates-itl-testing",
            env=env,
        )

        # The already-published RPM was uploaded to the remote ...
        assert (remote / "templates-itl-testing/rpm" / template_rpm).exists()
        # ... and nothing was (re)built: no build RPM artifacts appeared.
        assert not (artifacts_dir / "templates/rpm").exists()
