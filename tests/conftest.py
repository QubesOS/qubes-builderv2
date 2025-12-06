import os
import pathlib
import tempfile
import uuid

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--artifacts-dir",
        action="store",
        default=None,
        help="Directory to use as qb artifacts-dir",
    )


@pytest.fixture
def artifacts_dir_single():
    if os.environ.get("BASE_ARTIFACTS_DIR"):
        tmpdir = tempfile.mkdtemp(
            prefix="github-", dir=os.environ["BASE_ARTIFACTS_DIR"]
        )
    else:
        tmpdir = tempfile.mkdtemp(prefix="github-")
    artifacts_dir = pathlib.Path(tmpdir) / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    yield artifacts_dir


@pytest.fixture(scope="session")
def artifacts_dir(pytestconfig):
    cli_dir = pytestconfig.getoption("--artifacts-dir")

    if cli_dir:
        root = pathlib.Path(cli_dir)
    else:
        base = pathlib.Path(
            os.environ.get("BASE_ARTIFACTS_DIR", tempfile.gettempdir())
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
    yield root
