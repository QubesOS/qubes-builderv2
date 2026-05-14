import importlib
import os
import pathlib
import random
import shutil
import string
import subprocess
import tempfile
from argparse import Namespace

import pytest

from qubesbuilder.common import PROJECT_PATH

get_and_verify_source = importlib.import_module(
    "qubesbuilder.plugins.fetch.scripts.get-and-verify-source"
).main


@pytest.fixture
def temp_directory():
    # Create a temporary directory
    temp_dir = pathlib.Path(tempfile.mkdtemp())
    yield temp_dir
    # Remove the temporary directory after the test
    if temp_dir.exists():
        shutil.rmtree(temp_dir)


@pytest.fixture
def home_directory(temp_directory):
    gnupghome = f"{temp_directory}/gnupg"
    shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
    os.chmod(gnupghome, 0o700)
    # Initialize the conf
    with open(f"{temp_directory}/.gitconfig", "w") as gitconfig:
        gitconfig.write(
            "[user]\nname=testuser\nemail=test@localhost\n[gpg]\nprogram=gpg2"
        )
    yield temp_directory


def create_git_repository(repo_dir, env, sign_commit=False, sign_key=None):
    # Initialize the repository
    subprocess.run(
        [
            "git",
            "clone",
            "https://github.com/qubesos/qubes-core-qrexec",
            repo_dir,
        ],
        check=True,
        capture_output=True,
        env=env,
    )

    if sign_commit and sign_key:
        subprocess.run(
            ["git", "config", "user.signingkey", sign_key],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=repo_dir,
        )

    # Add some commits to the repository
    for i in range(1, 5):
        with open(repo_dir / f"file{i}.txt", "w") as file:
            file.write(f"Content of file {i}")
        subprocess.run(
            ["git", "add", f"file{i}.txt"], check=True, cwd=repo_dir, env=env
        )
        commit_cmd = ["git", "commit", "-m", f"Commit {i}"]
        if sign_commit and sign_key:
            commit_cmd += ["-S"]
        subprocess.run(
            commit_cmd,
            check=True,
            capture_output=True,
            cwd=repo_dir,
            env=env,
        )


def export_key_to_file(key_id, output_dir, gnupg_dir):
    result = subprocess.run(
        ["gpg", "--export", "--armor", key_id],
        capture_output=True,
        text=True,
        check=True,
        env={"GNUPGHOME": gnupg_dir},
    )
    exported_key = result.stdout.strip()
    fingerprint = key_id
    output_filename = f"{output_dir}/{fingerprint}.asc"
    with open(output_filename, "w") as fd:
        fd.write(exported_key)


def create_keys_dir(destination_dir, key_ids, gnupg_dir):
    shutil.copytree(
        PROJECT_PATH / "qubesbuilder/plugins/fetch/keys", destination_dir
    )
    for key_id in key_ids:
        export_key_to_file(key_id, destination_dir, gnupg_dir)


def get_random_string(length):
    return "".join(random.choice(string.ascii_lowercase) for _ in range(length))


#
# get_and_verify_source
#


def create_dummy_args(
    component_repository,
    component_directory,
    git_branch="main",
    git_commit=None,
    shallow_clone=False,
    keys_dir=str(PROJECT_PATH / "qubesbuilder/plugins/fetch/keys"),
    fetch_only=False,
    fetch_versions_only=False,
    ignore_missing=False,
    clean=False,
    insecure_skip_checking=False,
    less_secure_signed_commits_sufficient=False,
    maintainers=None,
    minimum_distinct_maintainers=1,
    trust_all_keys=False,
):
    args = Namespace()

    if maintainers != []:
        # Add default maintainers being Marek and Simon if maintainers are
        # not explicitly an empty list
        maintainers = (maintainers or []) + [
            "0064428F455451B3EBE78A7F063938BA42CFA724",
            "274E12AB03F2FE293765FC06DA0434BC706E1FCF",
        ]
    else:
        maintainers = (
            maintainers if maintainers and isinstance(maintainers, list) else []
        )

    args.component_repository = component_repository
    args.component_directory = str(component_directory)
    args.keys_dir = keys_dir
    args.git_keyring_dir = str(component_directory / ".keyring")

    args.git_branch = git_branch
    args.git_commit = git_commit
    args.shallow_clone = shallow_clone
    args.fetch_only = fetch_only
    args.fetch_versions_only = fetch_versions_only
    args.ignore_missing = ignore_missing
    args.clean = clean
    args.insecure_skip_checking = insecure_skip_checking
    args.less_secure_signed_commits_sufficient = (
        less_secure_signed_commits_sufficient
    )
    args.maintainer = maintainers
    args.minimum_distinct_maintainers = minimum_distinct_maintainers
    args.trust_all_keys = trust_all_keys
    return args


def test_repository(temp_directory):
    args = create_dummy_args(
        component_repository="https://github.com/qubesos/qubes-core-vchan-xen",
        component_directory=temp_directory,
    )
    get_and_verify_source(args)
    assert (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            cwd=str(temp_directory),
            text=True,
        ).stdout.strip()
        == "main"
    )
    assert (temp_directory / "version").exists()


def test_repository_invalid_branch(temp_directory):
    args = create_dummy_args(
        component_repository="https://github.com/qubesos/qubes-core-vchan-xen",
        component_directory=temp_directory,
        git_branch="nonexistent",
    )
    with pytest.raises(subprocess.CalledProcessError) as e:
        get_and_verify_source(args)
    assert (
        b"fatal: Remote branch nonexistent not found in upstream origin\n"
        == e.value.stderr
    )


def test_non_qubesos_repository(capsys, temp_directory):
    args = create_dummy_args(
        component_directory=temp_directory,
        component_repository="https://github.com/fepitre/qubes-core-qrexec",
        git_branch="builderv2",
    )
    with pytest.raises(ValueError) as e:
        get_and_verify_source(args)
    (msg,) = e.value.args
    assert (
        msg == "---> Invalid commit 961bf3c7bd8cc5dc5335ee91afefbf7feb92c982."
    )


def test_non_qubesos_repository_with_maintainer_and_no_signed_tags(
    temp_directory,
):
    args = create_dummy_args(
        component_directory=temp_directory,
        component_repository="https://github.com/fepitre/qubes-core-qrexec",
        git_branch="builderv2",
        less_secure_signed_commits_sufficient=True,
        maintainers=["9FA64B92F95E706BF28E2CA6484010B5CDC576E2"],
    )
    get_and_verify_source(args)
    assert (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            cwd=str(temp_directory),
            text=True,
        ).stdout.strip()
        == "builderv2"
    )


def test_repository_no_version_tags(temp_directory):
    # We created a branch where we have deleted all version tags
    args = create_dummy_args(
        component_repository="https://github.com/fepitre/qubes-core-qrexec",
        component_directory=temp_directory,
        git_branch="builderv2-tests-1",
        fetch_versions_only=True,
    )
    with pytest.raises(ValueError) as e:
        get_and_verify_source(args)
    (msg,) = e.value.args
    assert "No version tag." == msg


def test_existing_repository_no_version_tags(capsys, temp_directory):
    component_repository = "https://github.com/fepitre/qubes-core-qrexec"
    git_branch = "builderv2-tests-1"
    subprocess.run(
        [
            "git",
            "clone",
            "-b",
            git_branch,
            component_repository,
            temp_directory,
        ],
        check=True,
        capture_output=True,
    )
    args = create_dummy_args(
        component_repository=component_repository,
        component_directory=temp_directory,
        git_branch=git_branch,
        fetch_versions_only=True,
    )
    get_and_verify_source(args)
    assert capsys.readouterr().out == "No version tag.\n"
    assert not (temp_directory / ".git/FETCH_HEAD").exists()


def test_existing_repository_with_version_tags(capsys, temp_directory):
    component_repository = "https://github.com/fepitre/qubes-core-qrexec"
    git_branch = "builderv2-tests-2"
    subprocess.run(
        [
            "git",
            "clone",
            "-b",
            git_branch,
            component_repository,
            temp_directory,
        ],
        check=True,
        capture_output=True,
    )
    args = create_dummy_args(
        component_repository=component_repository,
        component_directory=temp_directory,
        git_branch=git_branch,
        fetch_versions_only=True,
    )
    get_and_verify_source(args)
    assert (
        capsys.readouterr().out
        == "--> Verifying tags...\n---> Good tag 83fc687c10da7ca5625b13d4d1fadd400748c427.\n---> Good tag 76d34269f7910a17f186c4f819583faae404bb83.\n---> Good tag 644dd42e21b788abb7483c1ec0d4ab3a7d9d20aa.\nEnough distinct tag signatures. Found 1, mandatory minimum is 1.\n--> Merging...\n"
    )
    assert (temp_directory / ".git/FETCH_HEAD").exists()


def test_existing_repository_with_switching_branch(capsys, temp_directory):
    component_repository = "https://github.com/qubesos/qubes-core-qrexec"

    # Manually clone the repo
    subprocess.run(
        ["git", "clone", component_repository, temp_directory],
        check=True,
        capture_output=True,
    )

    # Ensure we are not on a version tag
    subprocess.run(
        ["git", "checkout", "53351ebf68d88e2fee06c761a9d5b1669b5713fc"],
        check=True,
        capture_output=True,
        cwd=temp_directory,
    )

    # Fetch latest version
    args = create_dummy_args(
        component_repository=component_repository,
        component_directory=temp_directory,
        fetch_versions_only=True,
        git_branch="release4.2",
    )
    get_and_verify_source(args)

    # Check if we have a version tag on HEAD
    vtag = subprocess.run(
        ["git", "tag", "--points-at=HEAD", "v*"],
        capture_output=True,
        text=True,
        cwd=temp_directory,
        check=True,
    ).stdout.strip()
    assert vtag.startswith("v")

    # Switch branch
    args = create_dummy_args(
        component_repository=component_repository,
        component_directory=temp_directory,
        git_branch="release4.1",
        fetch_versions_only=True,
    )
    get_and_verify_source(args)

    assert (
        "--> Switching branch from release4.2 branch to new release4.1\n--> Merging...\n"
        in capsys.readouterr().out
    )
    assert (temp_directory / ".git/FETCH_HEAD").exists()

    # Check if we have a version tag on HEAD
    vtag = subprocess.run(
        ["git", "tag", "--points-at=HEAD", "v*"],
        capture_output=True,
        text=True,
        cwd=temp_directory,
        check=True,
    ).stdout.strip()
    assert vtag.startswith("v")


def test_non_qubesos_repository_with_maintainer_and_signed_tag(
    temp_directory,
):
    args = create_dummy_args(
        component_directory=temp_directory,
        component_repository="https://github.com/fepitre/qubes-core-qrexec",
        git_branch="builderv2-tests-3",
        maintainers=["9FA64B92F95E706BF28E2CA6484010B5CDC576E2"],
    )
    get_and_verify_source(args)
    assert (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            cwd=str(temp_directory),
            text=True,
        ).stdout.strip()
        == "builderv2-tests-3"
    )


def test_repository_with_multiple_distinct_signatures(
    temp_directory, home_directory
):
    gnupg_dir = home_directory / "gnupg"
    remote_repo_dir = temp_directory / "remote_repo"
    repo_dir = temp_directory / "repo"
    key_ids = [
        "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
        "466110A602D13C7A5CD9DDF690A99E7695483BFE",
        "C1261D4BA94026D4EEBDCB485811E93DE307C3CE",
    ]
    env = {"GNUPGHOME": gnupg_dir, "HOME": home_directory}
    create_git_repository(remote_repo_dir, env)
    create_keys_dir(home_directory / "keys", key_ids, gnupg_dir)

    for key_id in key_ids:
        subprocess.run(
            ["git", "config", "user.signingkey", key_id],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=remote_repo_dir,
        )
        subprocess.run(
            [
                "git",
                "tag",
                "-s",
                f"{get_random_string(8)}",
                "-m",
                f"Tag from {key_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=remote_repo_dir,
        )
    args = create_dummy_args(
        component_directory=repo_dir,
        component_repository=f"file://{remote_repo_dir}/",
        keys_dir=home_directory / "keys",
        minimum_distinct_maintainers=3,
        maintainers=[
            "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",  # Key 1
            "466110A602D13C7A5CD9DDF690A99E7695483BFE",  # Key 2
            "C1261D4BA94026D4EEBDCB485811E93DE307C3CE",  # Key 3
        ],
    )
    get_and_verify_source(args)


def test_repository_with_multiple_non_distinct_signatures(
    temp_directory, home_directory
):
    gnupg_dir = home_directory / "gnupg"
    remote_repo_dir = temp_directory / "remote_repo"
    repo_dir = temp_directory / "repo"
    key_ids = [
        "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
        "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
        "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
        "466110A602D13C7A5CD9DDF690A99E7695483BFE",
    ]
    env = {"GNUPGHOME": gnupg_dir, "HOME": home_directory}
    create_git_repository(remote_repo_dir, env)
    create_keys_dir(home_directory / "keys", key_ids, gnupg_dir)

    for key_id in key_ids:
        subprocess.run(
            ["git", "config", "user.signingkey", key_id],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=remote_repo_dir,
        )
        subprocess.run(
            [
                "git",
                "tag",
                "-s",
                f"{get_random_string(8)}",
                "-m",
                f"Tag from {key_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=remote_repo_dir,
        )
    args = create_dummy_args(
        component_directory=repo_dir,
        component_repository=f"file://{remote_repo_dir}/",
        keys_dir=home_directory / "keys",
        minimum_distinct_maintainers=3,
        maintainers=[
            "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",  # Key 1
            "466110A602D13C7A5CD9DDF690A99E7695483BFE",  # Key 2
        ],
    )
    with pytest.raises(ValueError) as e:
        get_and_verify_source(args)
    (msg,) = e.value.args
    assert (
        msg
        == f"Not enough distinct tag signatures. Found 2, mandatory minimum is 3."
    )


def test_repository_with_multiple_distinct_signatures_not_in_maintainers(
    temp_directory, home_directory
):
    gnupg_dir = home_directory / "gnupg"
    remote_repo_dir = temp_directory / "remote_repo"
    repo_dir = temp_directory / "repo"
    key_ids = [
        "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
        "466110A602D13C7A5CD9DDF690A99E7695483BFE",
    ]
    env = {"GNUPGHOME": gnupg_dir, "HOME": home_directory}
    create_git_repository(remote_repo_dir, env)
    create_keys_dir(home_directory / "keys", key_ids, gnupg_dir)

    for key_id in key_ids:
        subprocess.run(
            ["git", "config", "user.signingkey", key_id],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=remote_repo_dir,
        )
        subprocess.run(
            [
                "git",
                "tag",
                "-s",
                f"{get_random_string(8)}",
                "-m",
                f"Tag from {key_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=remote_repo_dir,
        )
    args = create_dummy_args(
        component_directory=repo_dir,
        component_repository=f"file://{remote_repo_dir}/",
        keys_dir=home_directory / "keys",
        minimum_distinct_maintainers=1,
    )
    with pytest.raises(ValueError) as e:
        get_and_verify_source(args)
    (msg,) = e.value.args
    assert (
        msg
        == f"Not enough distinct tag signatures. Found 0, mandatory minimum is 1."
    )


def test_repository_with_signed_commit(capsys, temp_directory, home_directory):
    gnupg_dir = home_directory / "gnupg"
    remote_repo_dir = temp_directory / "remote_repo"
    repo_dir = temp_directory / "repo"
    key_ids = [
        "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
    ]
    env = {"GNUPGHOME": gnupg_dir, "HOME": home_directory}
    create_git_repository(
        remote_repo_dir,
        env,
        sign_commit=True,
        sign_key="8B080B3E649B153AA44FE43E722F2B7B164FDEF7",
    )
    create_keys_dir(home_directory / "keys", key_ids, gnupg_dir)

    args = create_dummy_args(
        component_directory=repo_dir,
        component_repository=f"file://{remote_repo_dir}/",
        keys_dir=home_directory / "keys",
        minimum_distinct_maintainers=3,
        maintainers=[
            "8B080B3E649B153AA44FE43E722F2B7B164FDEF7",  # Key 1
        ],
        less_secure_signed_commits_sufficient=True,
    )
    get_and_verify_source(args)
    assert (
        "does not have a signed tag. However, it is signed by a trusted key, and CHECK is set to signed-tag-or-commit. Accepting it anyway."
        in capsys.readouterr().out
    )


def test_repository_with_submodules(capsys, temp_directory, home_directory):
    args = create_dummy_args(
        component_repository="https://github.com/qubesos/qubes-vmm-xen-stubdom-linux",
        component_directory=temp_directory,
    )
    get_and_verify_source(args)
    assert "--> Updating submodules" in capsys.readouterr().out


def test_repository_fetch_version_tag_earlier(capsys, temp_directory):
    component_repository = "https://github.com/fepitre/qubes-core-qrexec"
    # Fresh clone on branch having signed tag not being version tag
    args = create_dummy_args(
        component_repository=component_repository,
        component_directory=temp_directory,
        git_branch="builderv2-tests-4",
        fetch_versions_only=True,
    )
    get_and_verify_source(args)

    # Check if we have a version tag on HEAD
    vtag = subprocess.run(
        ["git", "tag", "--points-at=HEAD", "v*"],
        capture_output=True,
        text=True,
        cwd=temp_directory,
        check=True,
    ).stdout.strip()
    assert vtag.startswith("v")


def test_repository_fetch_specific_commit(capsys, temp_directory):
    component_repository = "https://github.com/fepitre/qubes-core-qrexec"
    # Fresh clone on specific commit id, without tags
    commit = "de25326d41b1abf519e9a30c5760d56a1c816b3a"
    args = create_dummy_args(
        component_repository=component_repository,
        component_directory=temp_directory,
        git_commit="de25326d41b1abf519e9a30c5760d56a1c816b3a",
    )
    get_and_verify_source(args)

    # Check if we have a version tag on HEAD
    actual_commit = subprocess.run(
        ["git", "show", "--format=%H", "-s"],
        capture_output=True,
        text=True,
        cwd=temp_directory,
        check=True,
    ).stdout.strip()
    assert commit == actual_commit


def test_repository_trust_all_keys(temp_directory):
    args = create_dummy_args(
        component_repository="https://github.com/qubesos/qubes-core-vchan-xen",
        component_directory=temp_directory,
        trust_all_keys=True,
        maintainers=[],
    )
    get_and_verify_source(args)
    assert (
        subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            cwd=str(temp_directory),
            text=True,
        ).stdout.strip()
        == "main"
    )
    assert (temp_directory / "version").exists()


def test_repository_shallow_clone(temp_directory):
    args = create_dummy_args(
        component_repository="https://github.com/qubesos/qubes-core-vchan-xen",
        component_directory=temp_directory,
        shallow_clone=True,
    )
    get_and_verify_source(args)
    assert (
        subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            capture_output=True,
            cwd=str(temp_directory),
            text=True,
        ).stdout.strip()
        == "true"
    )
    assert (temp_directory / "version").exists()
