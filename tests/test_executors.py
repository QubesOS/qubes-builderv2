import os.path
import subprocess
import tempfile
from pathlib import Path, PurePath
from unittest.mock import MagicMock, call, patch

import pytest

from qubesbuilder.exc import QubesBuilderError
from qubesbuilder.executors import Executor, ExecutorError
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.executors.qubes import LinuxQubesExecutor
from qubesbuilder.executors.windows import SSHWindowsExecutor


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
        copy_in = [(hello, executor.get_builder_dir() / "tmp")]

        # Copy-out the modified file
        copy_out = [
            (executor.get_builder_dir() / "tmp/hello.md", Path(temp_dir))
        ]
        # Command that appends a line to the file
        cmd = [f"echo It works! >> {executor.get_builder_dir()}/tmp/hello.md"]

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
        copy_in = [(hello, executor.get_builder_dir() / "tmp")]

        # Copy-out the modified file
        copy_out = [
            (executor.get_builder_dir() / "tmp/hello.md", Path(temp_dir))
        ]
        # Command that appends a line to the file
        cmd = [
            f"echo ${{MY_ANSWER}} >> {executor.get_builder_dir()}/tmp/hello.md",
            f"echo ${{MY_QUESTION}} >> {executor.get_builder_dir()}/tmp/hello.md",
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
    executor.cleanup()


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


# SSHWindowsExecutor unit tests


def _make_ssh_executor(**kwargs) -> SSHWindowsExecutor:
    """Create an SSHWindowsExecutor with test defaults."""
    defaults = dict(ssh_ip="10.0.0.1", user="user", ssh_key_path="/tmp/key")
    defaults.update(kwargs)
    return SSHWindowsExecutor(**defaults)


def test_ssh_windows_invalid_ewdk_mode():
    with pytest.raises(ExecutorError, match="Invalid ewdk-mode"):
        _make_ssh_executor(ewdk_mode="invalid")


def test_ssh_windows_ewdk_mode_attach_default():
    ex = _make_ssh_executor()
    assert ex.ewdk_mode == "attach"


def test_ssh_windows_ewdk_mode_copy():
    ex = _make_ssh_executor(ewdk_mode="copy")
    assert ex.ewdk_mode == "copy"


def test_ssh_windows_attach_without_ssh_vm_raises():
    with pytest.raises(ExecutorError, match="requires ssh-vm"):
        _make_ssh_executor(
            ewdk="/some/ewdk.iso", ewdk_mode="attach", ssh_vm=None
        )


def test_ssh_windows_copy_without_ssh_vm_ok():
    # copy mode is valid without ssh-vm (plain SSH machine)
    ex = _make_ssh_executor(
        ewdk="/some/ewdk.iso", ewdk_mode="copy", ssh_vm=None
    )
    assert ex.ewdk_mode == "copy"


def test_ssh_windows_attach_without_ssh_vm_no_ewdk_ok():
    # no ewdk set — attach mode without ssh-vm is fine (nothing to attach)
    ex = _make_ssh_executor(ewdk_mode="attach", ssh_vm=None)
    assert ex.ewdk_mode == "attach"


def test_ssh_base_cmd_structure():
    ex = _make_ssh_executor(
        ssh_ip="1.2.3.4", user="bob", ssh_key_path="/id_rsa"
    )
    base = ex._ssh_base_cmd()
    assert base[0] == "ssh"
    assert "-i" in base
    assert "/id_rsa" in base
    assert "bob@1.2.3.4" in base
    assert "BatchMode yes" in base
    assert "StrictHostKeyChecking accept-new" in base


@patch("qubesbuilder.executors.windows.vm_state", return_value="Running")
@patch("qubesbuilder.executors.windows.start_vm")
def test_start_worker_skips_start_when_already_running(
    mock_start_vm, mock_vm_state
):
    ex = _make_ssh_executor(ssh_vm="win-build")

    with patch.object(ex, "ssh_cmd") as mock_ssh:
        ex.start_worker()

    mock_start_vm.assert_not_called()
    mock_ssh.assert_called_once_with(["exit 0"])


@patch("qubesbuilder.executors.windows.vm_state", return_value="Halted")
@patch("qubesbuilder.executors.windows.start_vm")
def test_start_worker_reraises_start_error(mock_start_vm, mock_vm_state):
    ex = _make_ssh_executor(ssh_vm="win-build")
    mock_start_vm.side_effect = ExecutorError("SomethingElseWentWrong")

    with patch.object(ex, "ssh_cmd"):
        with pytest.raises(ExecutorError, match="SomethingElseWentWrong"):
            ex.start_worker()


@patch("qubesbuilder.executors.windows.vm_state", return_value="Halted")
@patch("qubesbuilder.executors.windows.start_vm")
def test_start_worker_copy_mode_calls_setup_remote(
    mock_start_vm, mock_vm_state, tmp_path
):
    iso = tmp_path / "ewdk.iso"
    iso.write_bytes(b"iso")
    ex = _make_ssh_executor(ssh_vm="win-build", ewdk=str(iso), ewdk_mode="copy")

    with patch.object(ex, "ssh_cmd"), patch.object(
        ex, "setup_remote"
    ) as mock_setup:
        ex.start_worker()

    mock_setup.assert_called_once()


@patch("qubesbuilder.executors.windows.vm_state", return_value="Halted")
@patch("qubesbuilder.executors.windows.start_vm")
def test_start_worker_attach_mode_does_not_call_setup_remote(
    mock_start_vm, mock_vm_state, tmp_path
):
    iso = tmp_path / "ewdk.iso"
    iso.write_bytes(b"iso")
    ex = _make_ssh_executor(
        ssh_vm="win-build", ewdk=str(iso), ewdk_mode="attach"
    )

    with patch.object(ex, "ssh_cmd"), patch.object(
        ex, "setup_remote"
    ) as mock_setup, patch.object(ex, "attach_ewdk"):
        ex.start_worker()

    mock_setup.assert_not_called()


@patch("qubesbuilder.executors.windows.vm_state", return_value="Halted")
@patch("qubesbuilder.executors.windows.start_vm")
def test_start_worker_halted_attach_mode_assigns_before_start(
    mock_start_vm, mock_vm_state, tmp_path
):
    iso = tmp_path / "ewdk.iso"
    iso.write_bytes(b"iso")
    ex = _make_ssh_executor(
        ssh_vm="win-build", ewdk=str(iso), ewdk_mode="attach"
    )

    call_order = []
    with patch.object(ex, "ssh_cmd"), patch.object(
        ex,
        "attach_ewdk",
        side_effect=lambda vm, vm_running: call_order.append(
            ("attach_ewdk", vm_running)
        ),
    ):
        mock_start_vm.side_effect = lambda *a, **kw: call_order.append(
            ("start_vm",)
        )
        ex.start_worker()

    assert call_order[0] == ("attach_ewdk", False)  # assign before start
    assert call_order[1] == ("start_vm",)


@patch("qubesbuilder.executors.windows.vm_state", return_value="Running")
@patch("qubesbuilder.executors.windows.start_vm")
def test_start_worker_running_attach_mode_hotplugs(
    mock_start_vm, mock_vm_state, tmp_path
):
    iso = tmp_path / "ewdk.iso"
    iso.write_bytes(b"iso")
    ex = _make_ssh_executor(
        ssh_vm="win-build", ewdk=str(iso), ewdk_mode="attach"
    )

    with patch.object(ex, "ssh_cmd"), patch.object(
        ex, "attach_ewdk"
    ) as mock_attach:
        ex.start_worker()

    mock_start_vm.assert_not_called()
    mock_attach.assert_called_once_with(ex.vm, vm_running=True)
