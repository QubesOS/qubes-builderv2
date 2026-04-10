import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

import pytest

# Redirect all temporary files to home
_default_tmpdir = os.path.join(os.path.expanduser("~"), "qb-test-tmp")
os.makedirs(_default_tmpdir, exist_ok=True)
if tempfile.tempdir is None:
    tempfile.tempdir = os.environ.get("TMPDIR", _default_tmpdir)


def _seed_cache(cache_dir: pathlib.Path, artifacts_dir: pathlib.Path):
    """
    Hard-link (or copy) cache/chroot from cache_dir into artifacts_dir.
    """
    src = cache_dir / "cache" / "chroot"
    if not src.is_dir():
        return
    dst_parent = artifacts_dir / "cache"
    dst_parent.mkdir(parents=True, exist_ok=True)
    dst = dst_parent / "chroot"
    if dst.exists():
        return  # already seeded
    try:
        # Hard-link the tree to not use extra disk space
        subprocess.run(
            ["cp", "-al", str(src), str(dst_parent)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        # Fall back to a regular copy (e.g. cross-device).
        shutil.copytree(str(src), str(dst))


def pytest_addoption(parser):
    parser.addoption(
        "--artifacts-dir",
        action="store",
        default=None,
        help="Directory to use as qb artifacts-dir",
    )
    parser.addoption(
        "--cache-dir",
        action="store",
        default=None,
        help="Directory with pre-built chroot caches to seed into artifacts-dir",
    )


def _get_cache_dir(pytestconfig) -> pathlib.Path | None:
    opt = pytestconfig.getoption("--cache-dir") or os.environ.get(
        "CACHE_ARTIFACTS_DIR"
    )
    return pathlib.Path(opt) if opt else None


@pytest.fixture
def artifacts_dir_single(pytestconfig):
    cache_dir = _get_cache_dir(pytestconfig)
    if os.environ.get("SINGLE_ARTIFACTS_DIR"):
        artifacts_dir = pathlib.Path(os.environ["SINGLE_ARTIFACTS_DIR"])
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        if cache_dir:
            _seed_cache(cache_dir, artifacts_dir)
        yield artifacts_dir
        return
    if os.environ.get("BASE_ARTIFACTS_DIR"):
        tmpdir = tempfile.mkdtemp(
            prefix="github-", dir=os.environ["BASE_ARTIFACTS_DIR"]
        )
    else:
        default_base = os.path.join(
            os.path.expanduser("~"), "qb-test-artifacts"
        )
        os.makedirs(default_base, exist_ok=True)
        tmpdir = tempfile.mkdtemp(prefix="github-", dir=default_base)
    artifacts_dir = pathlib.Path(tmpdir) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    if cache_dir:
        _seed_cache(cache_dir, artifacts_dir)
    yield artifacts_dir


@pytest.fixture(scope="session")
def artifacts_dir(pytestconfig):
    cli_dir = pytestconfig.getoption("--artifacts-dir")
    cache_dir = _get_cache_dir(pytestconfig)

    if cli_dir:
        root = pathlib.Path(cli_dir)
    else:
        base = pathlib.Path(
            os.environ.get(
                "BASE_ARTIFACTS_DIR",
                os.path.join(os.path.expanduser("~"), "qb-test-artifacts"),
            )
        )
        base.mkdir(parents=True, exist_ok=True)

        cache_key = "qb/artifacts_dir_name"

        if os.environ.get("CLEAN_ARTIFACTS"):
            name = f"github-{uuid.uuid4().hex}"
            pytestconfig.cache.set(cache_key, name)
        else:
            name = pytestconfig.cache.get(cache_key, None)
            if name is None:
                name = f"github-{uuid.uuid4().hex}"
                pytestconfig.cache.set(cache_key, name)

        root = base / name / "artifacts"

    root.mkdir(parents=True, exist_ok=True)
    if cache_dir:
        _seed_cache(cache_dir, root)
    yield root
