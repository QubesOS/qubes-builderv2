import hashlib
import os.path
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

from qubesbuilder.common import PROJECT_PATH

DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder-ci.yml"
HASH_RE = re.compile(r"[a-f0-9]{40}")


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
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={artifacts_dir}",
        *args,
    ]
    return subprocess.check_output(cmd, **kwargs)


def test_repository_create_vm_fc40(artifacts_dir):
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
            "-c",
            "qubes-release",
            "package",
            "fetch",
            env=env,
        )

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-vchan-xen",
            "-d",
            "vm-fc40",
            "repository",
            "create",
            "current",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/r4.2/current/vm/fc40/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert (
            "/pub/os/qubes/repo/yum/r4.2/current/vm/fc40/repodata/repomd.xml"
            in (metadata_dir / "repomd.xml.metalink").read_text(
                encoding="ascii"
            )
        )


def test_repository_create_vm_bookworm(artifacts_dir):
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
                "-c",
                "core-vchan-xen",
                "-d",
                "vm-bookworm",
                "repository",
                "create",
                repo,
                env=env,
            )

        repository_dir = artifacts_dir / "repository-publish/deb/r4.2/vm"
        for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
            assert (repository_dir / "dists" / codename / "InRelease").exists()
            assert (
                repository_dir / "dists" / codename / "Release.gpg"
            ).exists()


def test_repository_create_template(artifacts_dir):
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
            "-t",
            "whonix-gateway-18",
            "repository",
            "create",
            "templates-community-testing",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/r4.2/templates-community-testing/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert "/pub/os/qubes/repo/yum/r4.2/templates-community-testing/repodata/repomd.xml" in (
            metadata_dir / "repomd.xml.metalink"
        ).read_text(
            encoding="ascii"
        )

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-xfce",
            "repository",
            "create",
            "templates-itl-testing",
            env=env,
        )

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/r4.2/templates-itl-testing/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert "/pub/os/qubes/repo/yum/r4.2/templates-itl-testing/repodata/repomd.xml" in (
            metadata_dir / "repomd.xml.metalink"
        ).read_text(
            encoding="ascii"
        )

        # ensure we don't have anything related to deb for template repository in clean artifacts dir
        assert not (artifacts_dir / "repository-publish/deb").exists()
