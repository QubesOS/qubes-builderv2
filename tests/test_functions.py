import tempfile
from pathlib import Path

import pytest

from qubesbuilder.cli.cli_main import parse_config_from_cli
from qubesbuilder.common import (
    is_filename_valid,
    deep_check,
    sed,
    get_archive_name,
)


def test_filename():
    assert is_filename_valid("xfwm4_4.14.2.orig.tar.bz2")
    assert not is_filename_valid("xfwm4_4$14.2.orig@tar.bz2")
    assert not is_filename_valid("-xfwm4_4.14.2.orig.tar.bz2")


def test_qubesbuilder_data():
    data = {
        "host": {"rpm": {"build": ["python-qasync.spec"]}},
        "vm": {
            "rpm": {"build": ["python-qasync.spec"]},
            "deb": {"build": ["debian-pkg/debian"]},
        },
        "source": {
            "files": [
                {
                    "url": "https://files.pythonhosted.org/packages/source/q/qasync/qasync-0.23.0.tar.gz",
                    "sha256": "qasync-0.23.0.tar.gz.sha256",
                }
            ]
        },
    }
    deep_check(data)

    data = {"a": "../.."}
    with pytest.raises(ValueError) as e:
        deep_check(data)
    assert ".." in e.value.args[0]

    data = {"..": "b"}
    with pytest.raises(ValueError) as e:
        deep_check(data)
    assert ".." in e.value.args[0]

    data = {"a": {"b": ["c", "d", {"e": "toto.build.yml"}]}}
    with pytest.raises(ValueError) as e:
        deep_check(data)
    assert ".build.yml" in e.value.args[0]

    data = {
        "host": {"rpm": {"build": ["python-qasync.spec"]}},
        "vm": {
            "rpm": {"build": ["python-qasync.spec"]},
            "deb": {"build": ["debian-pkg/debian"]},
        },
        "source": {
            "files": [
                {
                    "url": "https://files.pythonhosted.org/packages/source/q/qasync/qasync-0.23.0.tar.gz",
                    "sha256": "qasync-0.23.0.tar.gz.sha256",
                },
                {
                    "url": "https://malicious/url/../toto.tar.gz",
                    "sha256": "toto.tar.gz.sha256",
                },
            ]
        },
    }
    with pytest.raises(ValueError) as e:
        deep_check(data)
    assert ".." in e.value.args[0]

    data = {
        "host": {"rpm": {"build": ["python-qasync.spec"]}},
        "vm": {
            "rpm": {"build": ["python-qasync.spec"]},
            "deb": {"build": ["python-qasync.spec.fetch.yaml"]},
        },
        "source": {
            "files": [
                {
                    "url": "https://files.pythonhosted.org/packages/source/q/qasync/qasync-0.23.0.tar.gz",
                    "sha256": "qasync-0.23.0.tar.gz.sha256",
                },
            ]
        },
    }
    with pytest.raises(ValueError) as e:
        deep_check(data)
    assert ".fetch.yaml" in e.value.args[0]


def test_parse_config_entry_from_array_01():
    array = [
        "executor:type=qubes",
        "executor:options:dispvm=builder-dvm",
        "backend-vmm=kvm",
        "force-fetch=True",
    ]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "executor": {"type": "qubes", "options": {"dispvm": "builder-dvm"}},
        "backend-vmm": "kvm",
        "force-fetch": True,
    }

    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_02():
    array = [" =wrongkey"]
    with pytest.raises(ValueError) as e:
        parse_config_from_cli(array)
    assert " " in e.value.args[0]

    array = ["_=wrongkey"]
    with pytest.raises(ValueError) as e:
        parse_config_from_cli(array)
    assert "_" in e.value.args[0]


def test_parse_config_entry_from_array_03():
    array = ["components+kernel:branch=stable-5.15", "components+lvm2"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "components": [{"kernel": {"branch": "stable-5.15"}}, "lvm2"],
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_04():
    array = ["+components+kernel:branch=stable-5.15", "+components+lvm2"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "+components": [{"kernel": {"branch": "stable-5.15"}}, "lvm2"],
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_05():
    array = [
        "repository-upload-remote-host:iso=remote.host:/remote/dir/",
    ]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "repository-upload-remote-host": {"iso": "remote.host:/remote/dir/"},
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_06():
    array = ["tata:titi:toto=many=equals"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "tata": {"titi": {"toto": "many=equals"}},
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_07():
    array = ["tata:titi+toto"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "tata": {"titi": ["toto"]},
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_08():
    array = ["tata:titi+toto:some=thing"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {"tata": {"titi": [{"toto": {"some": "thing"}}]}}
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_09():
    array = ["+tata:titi+toto:some=thing"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {"+tata": {"titi": [{"toto": {"some": "thing"}}]}}
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_10():
    array = ["+tata:titi+toto"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "+tata": {"titi": ["toto"]},
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_11_merge_multiple():
    array = ["+tata:titi+toto", "+tata:titi+toto:foo=bar"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "+tata": {"titi": [{"toto": {"foo": "bar"}}]},
    }
    assert parsed_dict == expected_dict


def test_parse_config_entry_from_array_12_merge_multiple():
    array = ["+tata:titi+toto:foo=bar", "+tata:titi+toto"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "+tata": {"titi": [{"toto": {"foo": "bar"}}]},
    }
    assert parsed_dict == expected_dict

def test_parse_config_entry_from_array_13_merge_multiple():
    array = ["+tata:titi+toto:foo=bar", "+tata:titi+toto:foo=baz"]
    parsed_dict = parse_config_from_cli(array)
    expected_dict = {
        "+tata": {"titi": [{"toto": {"foo": "baz"}}]},
    }
    assert parsed_dict == expected_dict


def test_deep_check_dict():
    data = {
        "key1": "value1",
        "key2": "value2",
        "nested": {
            "key3": "value3",
            "key4": "value4",
        },
    }
    deep_check(data)  # No exception should be raised


def test_deep_check_list():
    data = [1, 2, [3, 4], [5, [6, 7]]]
    deep_check(data)  # No exception should be raised


def test_deep_check_str():
    data = "This is a .. test"
    with pytest.raises(ValueError):
        deep_check(data)  # Should raise ValueError for ".."


def test_deep_check_int():
    data = 123
    deep_check(data)  # No exception should be raised


def test_deep_check_unexpected():
    data = None
    with pytest.raises(ValueError):
        deep_check(data)  # Should raise ValueError for unexpected data type


def test_sed_with_destination():
    # Prepare a temporary source file for testing
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as source_file:
        source_file.write("Hello, World!")
        source_file.flush()

        # Define the test parameters
        pattern = r"Hello"
        replace = "Hi"
        destination = tempfile.NamedTemporaryFile(mode="w", delete=False).name

        # Execute the function being tested
        sed(pattern, replace, source_file.name, destination)

        # Check if the destination file contains the expected content
        with open(destination, "r") as fd:
            assert fd.read() == "Hi, World!"

        # Clean up the temporary files
        source_file.close()
        Path(source_file.name).unlink()
        Path(destination).unlink()


def test_sed_without_destination():
    # Prepare a temporary source file for testing
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as source_file:
        source_file.write("Hello, World!")
        source_file.flush()

        # Define the test parameters
        pattern = r"Hello"
        replace = "Hi"

        # Execute the function being tested
        sed(pattern, replace, source_file.name)

        # Check if the source file contains the expected content
        with open(source_file.name, "r") as fd:
            assert fd.read() == "Hi, World!"

        # Clean up the temporary file
        source_file.close()
        Path(source_file.name).unlink()


def test_get_archive_name_url():
    file = {
        "url": "https://example.com/some-file.tar.gz",
        "signature": "https://example.com/some-file.tar.gz.asc",
        "pubkeys": ["pubkey1.asc", "pubkey2.asc"],
    }
    fn = get_archive_name(file)
    assert fn == "some-file.tar.gz"


def test_get_archive_name_url_uncompress():
    file = {
        "url": "https://example.com/some-file.tar.gz",
        "signature": "https://example.com/some-file.tar.gz.asc",
        "uncompress": True,
        "pubkeys": ["pubkey1.asc", "pubkey2.asc"],
    }
    fn = get_archive_name(file)
    assert fn == "some-file.tar"


def test_get_archive_name_git_url():
    file = {
        "git-url": "https://github.com/owner/repo.git",
        "tag": "v1.0.0",
        "pubkeys": ["pubkey1.asc", "pubkey2.asc"],
    }
    fn = get_archive_name(file)
    assert fn == "repo-v1.0.0.tar.gz"

    file = {
        "git-url": "https://github.com/owner/repo",
        "commit-id": "0011223344556677889900112233445566778899",
    }
    fn = get_archive_name(file)
    assert fn == "repo-0011223344556677889900112233445566778899.tar.gz"

    file = {
        "git-url": "https://github.com/owner/repo",
        "tag": "v2.0.0",
        "git-basename": "repo-2.0.0",
    }
    fn = get_archive_name(file)
    assert fn == "repo-2.0.0.tar.gz"
