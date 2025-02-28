import os.path
import subprocess
import tempfile
from pathlib import Path, PurePath

import pytest

from qubesbuilder.exc import QubesBuilderError
from qubesbuilder.executors import Executor, ExecutorError
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.executors.qubes import LinuxQubesExecutor


class MockExecutor(Executor):
    def copy_in(self, *args, **kwargs):
        pass

    def copy_out(self, *args, **kwargs):
        pass

    def run(self, *args, **kwargs):
        pass

    def get_user(self):
        pass

    def get_group(self):
        pass


class MockExecutorWithError(Executor):
    def copy_in(self, *args, **kwargs):
        raise ExecutorError("Copy in error")

    def copy_out(self, *args, **kwargs):
        raise ExecutorError("Copy out error")

    def run(self, *args, **kwargs):
        raise ExecutorError("Run error")

    def get_user(self):
        raise NotImplementedError

    def get_group(self):
        raise NotImplementedError


def test_executor_get_builder_dir():
    executor = MockExecutor()
    assert executor.get_builder_dir() == Path("/builder")


def test_executor_get_build_dir():
    executor = MockExecutor()
    assert executor.get_build_dir() == Path("/builder/build")


def test_executor_get_plugins_dir():
    executor = MockExecutor()
    assert executor.get_plugins_dir() == Path("/builder/plugins")


def test_executor_get_sources_dir():
    executor = MockExecutor()
    assert executor.get_sources_dir() == Path("/builder/sources")


def test_executor_get_distfiles_dir():
    executor = MockExecutor()
    assert executor.get_distfiles_dir() == Path("/builder/distfiles")


def test_executor_get_repository_dir():
    executor = MockExecutor()
    assert executor.get_repository_dir() == Path("/builder/repository")


def test_executor_get_cache_dir():
    executor = MockExecutor()
    assert executor.get_cache_dir() == Path("/builder/cache")


def test_executor_get_user_not_implemented():
    executor = MockExecutorWithError()
    with pytest.raises(NotImplementedError):
        executor.get_user()


def test_executor_get_group_not_implemented():
    executor = MockExecutorWithError()
    with pytest.raises(NotImplementedError):
        executor.get_group()


def test_executor_get_placeholders():
    executor = MockExecutor()
    placeholders = executor.get_placeholders()

    assert placeholders["@BUILDER_DIR@"] == Path("/builder")
    assert placeholders["@BUILD_DIR@"] == Path("/builder/build")
    assert placeholders["@PLUGINS_DIR@"] == Path("/builder/plugins")
    assert placeholders["@DISTFILES_DIR@"] == Path("/builder/distfiles")


def test_executor_replace_placeholders():
    executor = MockExecutor()
    s = "@BUILDER_DIR@/@BUILD_DIR@"
    replaced_s = executor.replace_placeholders(s)

    assert replaced_s == "/builder//builder/build"


def test_executor_error():
    with pytest.raises(ExecutorError) as exc_info:
        raise ExecutorError("Test error")

    assert str(exc_info.value) == "Test error"
    assert isinstance(exc_info.value, QubesBuilderError)


# common tests


@pytest.fixture(params=["container", "local", "qubes"])
def executor(request):
    if request.param == "container":
        executor = ContainerExecutor(
            "docker", "fedora:latest", user="root", group="root"
        )
        yield executor
    elif request.param == "local":
        executor = LocalExecutor()
        yield executor
    elif request.param == "qubes":
        if not os.path.exists("/var/run/qubes/this-is-appvm"):
            pytest.skip("Qubes Admin VM is required!")
        executor = LinuxQubesExecutor(
            os.environ.get("QUBES_EXECUTOR_DISPVM", "builder-dvm")
        )
        yield executor
    else:
        assert False, f"Invalid param {request.param}"


def test_simple(executor):
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a local file with some content
        hello = Path(temp_dir) / "hello.md"
        with open(hello, "w") as f:
            f.write("Hello!\n")

        # Copy-in the previously created local file
        copy_in = [(hello, Path("/tmp"))]

        # Copy-out the modified file
        copy_out = [(Path("/tmp/hello.md"), Path(temp_dir))]
        # Command that appends a line to the file
        cmd = ["echo It works! >> /tmp/hello.md"]

        # Execute the command
        executor.run(cmd, copy_in, copy_out)

        # Read modified file that has been copied out from the container
        with open(hello) as f:
            data = f.read()

        # Check expected content
        assert data == "Hello!\nIt works!\n"


def test_environment(executor):
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a local file with some content
        hello = Path(temp_dir) / "hello.md"
        with open(hello, "w") as f:
            f.write("Hello!\n")

        # Copy-in the previously created local file
        copy_in = [(hello, Path("/tmp"))]

        # Copy-out the modified file
        copy_out = [(Path("/tmp/hello.md"), Path(temp_dir))]
        # Command that appends a line to the file
        cmd = [
            "echo ${MY_ANSWER} >> /tmp/hello.md",
            "echo ${MY_QUESTION} >> /tmp/hello.md",
        ]

        # Execute the command
        executor.run(
            cmd,
            copy_in,
            copy_out,
            environment={
                "MY_ANSWER": "Hi there!",
                "MY_QUESTION": "How are you?",
            },
        )

        # Read modified file that has been copied out from the container
        with open(hello) as f:
            data = f.read()

        # Check expected content
        assert data == "Hello!\nHi there!\nHow are you?\n"


def test_run_error(executor):
    # Command that fails
    cmd = ["false"]

    # Execute the command
    with pytest.raises(ExecutorError) as exc_info:
        executor.run(cmd, [], [])

    assert "Failed to run" in str(exc_info.value)


def test_copy_in_error(executor):
    # No-op command
    cmd = ["true"]

    # Execute the command
    with pytest.raises(ExecutorError) as exc_info:
        copy_in = [(Path("/no/such/file"), Path("/tmp"))]
        executor.run(cmd, copy_in, [])

    assert "Failed to copy-in" in str(exc_info.value)


def test_copy_out_error(executor):
    # No-op command
    cmd = ["true"]

    # Execute the command
    with pytest.raises(ExecutorError) as exc_info:
        copy_out = [(Path("/no/such/file"), Path("/tmp"))]
        executor.run(cmd, [], copy_out)

    assert "Failed to copy-out" in str(exc_info.value)


def test_copy_out_error_ignored(executor):
    # No-op command
    cmd = ["true"]

    # Execute the command
    copy_out = [(Path("/no/such/file_ignore"), Path("/tmp"))]
    patterns = ["_ignore"]
    executor.run(cmd, [], copy_out, no_fail_copy_out_allowed_patterns=patterns)


# executor specific tests


def test_container_not_running():
    with pytest.raises(ExecutorError) as e:
        ContainerExecutor(
            "docker", "fedora:latest", base_url="tcp://127.0.0.1:1234"
        )
    msg = "Cannot connect to container client."
    assert str(e.value) == msg


def test_container_unknown_image():
    with pytest.raises(ExecutorError) as e:
        ContainerExecutor("docker", "fedora-unknown:latest")
    msg = "Cannot find fedora-unknown:latest."
    assert str(e.value) == msg


def test_container_clean_on_error():
    executor = ContainerExecutor(
        "docker", "fedora:latest", user="root", group="root"
    )
    cmd = "this_command_does_not_exist"
    with pytest.raises(ExecutorError) as e:
        executor.run([cmd])

    containerid = e.value.kwargs.get("name", None)
    assert containerid

    with executor.get_client() as client:
        assert containerid not in [
            c.id for c in client.containers.list(all=True)
        ]


def test_container_on_error_noclean():
    executor = ContainerExecutor(
        "docker",
        "fedora:latest",
        user="root",
        group="root",
        clean_on_error=False,
    )
    cmd = "this_command_does_not_exist"
    with pytest.raises(ExecutorError) as e:
        executor.run([cmd])

    containerid = e.value.kwargs.get("name", None)
    assert containerid

    with executor.get_client() as client:
        container = client.containers.get(containerid)

    assert container is not None
    executor.cleanup(container)


def test_local_clean_on_error():
    executor = LocalExecutor()
    cmd = "this_command_does_not_exist"
    with pytest.raises(ExecutorError):
        executor.run([cmd])

    assert not executor._temporary_dir.exists()


def test_local_on_error_noclean():
    executor = LocalExecutor(clean_on_error=False)
    cmd = "this_command_does_not_exist"
    with pytest.raises(ExecutorError):
        executor.run([cmd])

    assert executor._temporary_dir.exists()
    executor.cleanup()


def test_qubes_clean_on_error():
    executor = LinuxQubesExecutor(
        os.environ.get("QUBES_EXECUTOR_DISPVM", "builder-dvm")
    )
    cmd = "this_command_does_not_exist"
    with pytest.raises(ExecutorError) as e:
        executor.run([cmd])

    dispvm = e.value.kwargs.get("name", None)
    assert dispvm

    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["qvm-run-vm", "--", dispvm, "true"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
        )


def test_qubes_on_error_noclean():
    executor = LinuxQubesExecutor(
        os.environ.get("QUBES_EXECUTOR_DISPVM", "builder-dvm"),
        clean_on_error=False,
    )

    with pytest.raises(ExecutorError) as e:
        executor.run(["this_command_does_not_exist"])

    dispvm = e.value.kwargs.get("name", None)
    assert dispvm

    qubes = subprocess.run(
        ["qrexec-client-vm", "--", dispvm, "admin.vm.List"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert f"{dispvm} class=DispVM state=Running" in qubes

    subprocess.run(
        ["qvm-run-vm", "--", dispvm, "true"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )

    executor.cleanup()
