import hashlib
import os.path
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest
import yaml

from qubesbuilder.common import PROJECT_PATH

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
