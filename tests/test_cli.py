import datetime
import hashlib
import io
import itertools
import os.path
import pathlib
import re
import shutil
import subprocess
import tempfile

import dnf
import pytest
import yaml
from dateutil.parser import parse as parsedate
from pycdlib import pycdlib

from qubesbuilder.common import PROJECT_PATH

DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder-ci.yml"
HASH_RE = re.compile(r"[a-f0-9]{40}")


def _artifacts_dir():
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


@pytest.fixture
def artifacts_dir_single():
    yield from _artifacts_dir()


@pytest.fixture(scope="session")
def artifacts_dir():
    yield from _artifacts_dir()


def qb_call(builder_conf, artifacts_dir, *args, **kwargs):
    cmd = [
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={str(artifacts_dir)}",
        *args,
    ]
    return subprocess.check_call(cmd, **kwargs)


def qb_call_output(builder_conf, artifacts_dir, *args, **kwargs):
    cmd = [
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={str(artifacts_dir)}",
        *args,
    ]
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, **kwargs)


def deb_packages_list(repository_dir, suite, **kwargs):
    return (
        subprocess.check_output(
            ["reprepro", "-b", repository_dir, "list", suite],
            **kwargs,
        )
        .decode()
        .splitlines()
    )


def rpm_packages_list(repository_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        base = dnf.Base()
        base.conf.installroot = tmpdir
        base.conf.cachedir = tmpdir + "/cache"
        base.repos.add_new_repo(
            repoid="local", conf=base.conf, baseurl=[repository_dir]
        )
        base.fill_sack()
        q = base.sack.query()
        return [str(p) + ".rpm" for p in q.available()]


#
# config
#


def test_common_config(artifacts_dir):
    with tempfile.TemporaryDirectory() as tmpdir:
        include_path = os.path.join(tmpdir, "include1.yml")
        with open(include_path, "w") as f:
            f.write("+components:\n")
            f.write("- component2\n")
            f.write("- component3\n")
            f.write("distributions:\n")
            f.write("- vm-fc40\n")
            f.write("- vm-fc37\n")
        include_path = os.path.join(tmpdir, "include-nested.yml")
        with open(include_path, "w") as f:
            f.write("+components:\n")
            f.write("- component4\n")
            f.write("- component5\n")
            f.write("debug: true\n")
        include_path = os.path.join(tmpdir, "include2.yml")
        with open(include_path, "w") as f:
            f.write("include:\n")
            f.write("- include-nested.yml\n")
            f.write("+components:\n")
            f.write("- component6\n")
            f.write("- component7\n")
            f.write("distributions:\n")
            f.write("- vm-fc40\n")
            f.write("- vm-fc37\n")
        config_path = os.path.join(tmpdir, "builder.yml")
        with open(config_path, "w") as f:
            f.write("include:\n")
            f.write("- include1.yml\n")
            f.write("- include2.yml\n")
            f.write("components:\n")
            f.write("- component1\n")
            f.write("- component2\n")
            f.write("distributions:\n")
            f.write("- vm-fc33\n")
            f.write("- vm-fc34\n")

        output = qb_call_output(
            config_path, artifacts_dir, "config", "get-components"
        )
        assert (
            output == b"component1\ncomponent2\ncomponent3\ncomponent4\n"
            b"component5\ncomponent6\ncomponent7\n"
        )

        output = qb_call_output(
            config_path, artifacts_dir, "config", "get-distributions"
        )
        assert output == b"vm-fc33\nvm-fc34\n"

        output = qb_call_output(
            config_path, artifacts_dir, "config", "get-var", "debug"
        )
        assert output == b"true\n"


#
# Fetch
#


def _get_infos(artifacts_dir):
    infos = {}
    for component in [
        "core-qrexec",
        "core-vchan-xen",
        "desktop-linux-xfce4-xfwm4",
        "python-qasync",
        "app-linux-split-gpg",
    ]:
        qb_file = artifacts_dir / "sources" / component / ".qubesbuilder"
        dir_fd = os.open(artifacts_dir / "sources" / component, os.O_RDONLY)
        btime = subprocess.run(
            ["stat", "-c", "%W", str(qb_file)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        infos[component] = {
            "btime": btime,
            "fd": dir_fd,
            "list": os.listdir(dir_fd),
        }
    return infos


def test_common_component_fetch(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "package",
        "fetch",
    ).decode()

    assert (
        artifacts_dir / "distfiles/python-qasync/qasync-0.23.0.tar.gz"
    ).exists()
    assert (
        artifacts_dir
        / "distfiles/desktop-linux-xfce4-xfwm4/xfwm4-4.16.1.tar.bz2"
    ).exists()
    assert (artifacts_dir / "distfiles/linux-gbulb/gbulb-0.6.3.tar.gz").exists()
    # verify files layout inside
    subprocess.run(
        [
            "tar",
            "tf",
            artifacts_dir / "distfiles/linux-gbulb/gbulb-0.6.3.tar.gz",
            "gbulb-0.6.3/README.rst",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    for component in [
        "core-qrexec",
        "core-vchan-xen",
        "desktop-linux-xfce4-xfwm4",
        "python-qasync",
        "app-linux-split-gpg",
    ]:
        assert (
            artifacts_dir / "sources" / component / ".qubesbuilder"
        ).exists()
    assert (
        "Enough distinct tag signatures. Found 3, mandatory minimum is 3."
        in result
    )


def test_common_component_fetch_updating(artifacts_dir):
    infos_before = _get_infos(artifacts_dir)

    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "package",
        "fetch",
    ).decode()
    for sentence in [
        "python-qasync: source already fetched. Updating.",
        "core-vchan-xen: source already fetched. Updating.",
        "core-qrexec: source already fetched. Updating.",
        "app-linux-split-gpg: source already fetched. Updating.",
        "desktop-linux-xfce4-xfwm4: source already fetched. Updating.",
        "python-qasync: file qasync-0.23.0.tar.gz already downloaded. Skipping.",
        "desktop-linux-xfce4-xfwm4: file xfwm4-4.16.1.tar.bz2 already downloaded. Skipping.",
    ]:
        assert sentence in result

    infos_after = _get_infos(artifacts_dir)

    for component in infos_before:
        assert (
            infos_after[component]["btime"] != infos_before[component]["btime"]
        )


def test_common_component_fetch_inplace(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "git-run-inplace=true",
        "package",
        "fetch",
    ).decode()

    assert (
        artifacts_dir / "distfiles/python-qasync/qasync-0.23.0.tar.gz"
    ).exists()
    assert (
        artifacts_dir
        / "distfiles/desktop-linux-xfce4-xfwm4/xfwm4-4.16.1.tar.bz2"
    ).exists()

    for component in [
        "core-qrexec",
        "core-vchan-xen",
        "desktop-linux-xfce4-xfwm4",
        "python-qasync",
        "app-linux-split-gpg",
    ]:
        assert (
            artifacts_dir / "sources" / component / ".qubesbuilder"
        ).exists()
    assert (
        "Enough distinct tag signatures. Found 3, mandatory minimum is 3."
        in result
    )


def test_common_component_fetch_inplace_updating(artifacts_dir):
    infos_before = _get_infos(artifacts_dir)

    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "git-run-inplace=true",
        "package",
        "fetch",
    ).decode()

    for sentence in [
        "python-qasync: source already fetched. Updating.",
        "core-vchan-xen: source already fetched. Updating.",
        "core-qrexec: source already fetched. Updating.",
        "app-linux-split-gpg: source already fetched. Updating.",
        "desktop-linux-xfce4-xfwm4: source already fetched. Updating.",
        "python-qasync: file qasync-0.23.0.tar.gz already downloaded. Skipping.",
        "desktop-linux-xfce4-xfwm4: file xfwm4-4.16.1.tar.bz2 already downloaded. Skipping.",
    ]:
        assert sentence in result

    infos_after = _get_infos(artifacts_dir)

    for component in infos_before:
        assert (
            infos_after[component]["btime"] == infos_before[component]["btime"]
        )
        assert os.listdir(infos_after[component]["fd"]) == os.listdir(
            infos_before[component]["fd"]
        )


def test_common_component_fetch_skip_files(artifacts_dir_single):
    artifacts_dir = artifacts_dir_single

    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "skip-files-fetch=true",
        "package",
        "fetch",
    ).decode()

    infos = _get_infos(artifacts_dir)

    for component in [
        "core-qrexec",
        "core-vchan-xen",
        "desktop-linux-xfce4-xfwm4",
        "python-qasync",
        "app-linux-split-gpg",
    ]:
        assert (
            artifacts_dir / "sources" / component / ".qubesbuilder"
        ).exists()
        assert not list((artifacts_dir / "distfiles" / component).iterdir())
    assert (
        "Enough distinct tag signatures. Found 3, mandatory minimum is 3."
        in result
    )


def test_common_component_fetch_commit_fresh(artifacts_dir_single):
    artifacts_dir = artifacts_dir_single
    commit_sha = "0589ae8a242b3be6a1b8985c6eb8900e5236152a"
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "core-qrexec",
        "-o",
        f"+components+core-qrexec:branch={commit_sha}",
        "package",
        "fetch",
    ).decode()

    fetch_artifact = (
        artifacts_dir
        / "components/core-qrexec/4.2.20-1/nodist/fetch/source.fetch.yml"
    )
    assert fetch_artifact.exists()
    with open(fetch_artifact) as f:
        info = yaml.safe_load(f.read())
    assert info["git-commit-hash"] == commit_sha


def test_common_existent_command(artifacts_dir):
    result = qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "config",
        "get-components",
        "get-templates",
    )
    assert result == 0


def test_common_non_existent_command(artifacts_dir):
    with pytest.raises(subprocess.CalledProcessError):
        result = qb_call(
            DEFAULT_BUILDER_CONF, artifacts_dir, "non-existent-command"
        )
        assert result == 2


def test_common_non_existent_component(artifacts_dir):
    with pytest.raises(subprocess.CalledProcessError):
        result = qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "non-existent-component",
            "package",
            "all",
        )
        assert result == 2


def test_common_component_dependencies_01(artifacts_dir_single):
    with pytest.raises(subprocess.CalledProcessError) as e:
        qb_call_output(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "package",
            "prep",
        )
    assert (
        b"<JobReference(component=core-qrexec, stage=fetch, build=source)>"
        in e.value.output
    )


def test_common_component_dependencies_02(artifacts_dir_single):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir_single,
        "-c",
        "core-qrexec",
        "-d",
        "host-fc37",
        "package",
        "fetch",
    )

    with pytest.raises(subprocess.CalledProcessError) as e:
        qb_call_output(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "package",
            "build",
        )
    assert (
        b"<JobReference(component=core-qrexec, dist=host-fc37, stage=prep, build=rpm_spec_qubes-qrexec.spec)>"
        in e.value.output
    )


def test_common_component_dependencies_03(artifacts_dir_single):
    artifacts_dir = artifacts_dir_single
    dist_artifacts_dir = (
        artifacts_dir / "components" / "dummy-component" / "1.2.3-4"
    )

    # Directories for vm-fc40
    fc40_dir = dist_artifacts_dir / "vm-fc40" / "build"
    fc40_dir.mkdir(parents=True, exist_ok=True)

    # Create empty file
    (fc40_dir / "dummy.spec.build.yml").write_text(
        yaml.dump({"files": ["some.exe"]})
    )

    # Write executable content to "some.exe"
    (fc40_dir / "some.exe").write_text("some executable")

    # Directories for vm-fc41
    fc41_dir = dist_artifacts_dir / "vm-fc41" / "build"
    fc41_dir.mkdir(parents=True, exist_ok=True)

    # Create empty file
    (fc41_dir / "dummy.spec.build.yml").write_text(
        yaml.dump({"files": ["another.exe"]})
    )

    # Write executable content to "another.exe"
    (fc41_dir / "another.exe").write_text("another executable")

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "dummy-component",
        "-d",
        "host-fc37",
        "package",
        "fetch",
        "prep",
        "build",
    )

    rpm = (
        dist_artifacts_dir
        / "host-fc37"
        / "build"
        / "rpm"
        / "qubes-windows-tools-1.2.3-4.fc37.noarch.rpm"
    )
    p1 = subprocess.Popen(
        ["rpm2cpio", str(rpm)],
        cwd=str(dist_artifacts_dir),
        stdout=subprocess.PIPE,
    )
    p2 = subprocess.Popen(
        ["cpio", "-idmv"], cwd=str(dist_artifacts_dir), stdin=p1.stdout
    )
    p1.stdout.close()
    p2.communicate()

    iso_path = (
        dist_artifacts_dir / "usr" / "lib" / "qubes" / "qubes-windows-tools.iso"
    )
    assert (
        iso_path.exists()
    ), f"ISO file not found at expected location: {iso_path}"

    def get_joliet_filenames(iso):
        children = iso.list_children(joliet_path="/")
        filenames = []
        for child in children:
            raw = child.file_identifier()
            try:
                name = raw.decode("utf-16-be")
            except Exception:
                name = raw.decode("latin1")
            filenames.append(name)
        return filenames

    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    expected_files = {
        "qubes-tools-win10-1.2.3-4.exe": "some executable",
        "qubes-tools-win11-1.2.3-4.exe": "another executable",
    }
    filenames = get_joliet_filenames(iso)

    for expected_name, expected_content in expected_files.items():
        if expected_name not in filenames:
            iso.close()
            raise AssertionError(
                f"Expected file '{expected_name}' not found in ISO. Found files: {filenames}"
            )
        fp = io.BytesIO()
        joliet_path = "/" + expected_name
        try:
            iso.get_file_from_iso_fp(fp, joliet_path=joliet_path)
            fp.seek(0)
            content = fp.read().decode("utf-8").strip()
        except pycdlib.pycdlibexception.PyCdlibException:
            iso.close()
            raise AssertionError(
                f"Expected file '{joliet_path}' not found in ISO."
            )
        assert (
            content == expected_content
        ), f"Content mismatch for {expected_name}: expected '{expected_content}', got '{content}'"

    iso.close()


#
# Pipeline for core-qrexec and host-fc37
#


def test_component_host_fc37_init_cache(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "host-fc37",
        "package",
        "init-cache",
    )
    assert (artifacts_dir / "cache/chroot/fc37/mock/fedora-37-x86_64").exists()


def test_component_host_fc37_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "qubes-release",
        "-c",
        "core-qrexec",
        "-d",
        "host-fc37",
        "package",
        "fetch",
        "prep",
    )

    with open(
        artifacts_dir
        / "components/core-qrexec/4.2.18-1/host-fc37/prep/rpm_spec_qubes-qrexec.spec.prep.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    rpms = {
        "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
    }
    srpm = "qubes-core-qrexec-4.2.18-1.fc37.src.rpm"

    assert set(info.get("rpms", [])) == rpms
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == srpm

    with open(
        artifacts_dir
        / "components/core-qrexec/4.2.18-1/host-fc37/prep/rpm_spec_qubes-qrexec-dom0.spec.prep.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    rpms = {
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
    }
    srpm = "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm"

    assert set(info.get("rpms", [])) == rpms
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == srpm


def test_component_host_fc37_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "core-qrexec",
        "-d",
        "host-fc37",
        "package",
        "build",
    )

    with open(
        artifacts_dir
        / "components/core-qrexec/4.2.18-1/host-fc37/build/rpm_spec_qubes-qrexec.spec.build.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    rpms = {
        "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
    }
    srpm = "qubes-core-qrexec-4.2.18-1.fc37.src.rpm"

    for rpm in rpms.union({srpm}):
        assert (
            artifacts_dir / "repository/host-fc37/core-qrexec_4.2.18" / rpm
        ).exists()

    assert set(info.get("rpms", [])) == rpms
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == srpm

    with open(
        artifacts_dir
        / "components/core-qrexec/4.2.18-1/host-fc37/build/rpm_spec_qubes-qrexec-dom0.spec.build.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    rpms = {
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
    }
    srpm = "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm"

    for rpm in rpms.union({srpm}):
        assert (
            artifacts_dir / "repository/host-fc37/core-qrexec_4.2.18" / rpm
        ).exists()

    assert set(info.get("rpms", [])) == rpms
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == srpm

    # buildinfo
    assert (
        artifacts_dir
        / "components/core-qrexec/4.2.18-1/host-fc37/build/rpm/qubes-core-qrexec-4.2.18-1.fc37.x86_64.buildinfo"
    ).exists()


def test_component_host_fc37_sign(artifacts_dir):
    env = os.environ.copy()

    buildinfo = (
        artifacts_dir
        / "components/core-qrexec/4.2.18-1/host-fc37/build/rpm/qubes-core-qrexec-4.2.18-1.fc37.x86_64.buildinfo"
    )
    buildinfo_number_lines = len(
        buildinfo.read_text(encoding="utf8").splitlines()
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        # Better copy testing keyring into a separate directory to prevent locks inside
        # local sources (when executed locally).
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        # Enforce keyring location
        env["GNUPGHOME"] = gnupghome
        # We prevent rpm to find ~/.rpmmacros
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "package",
            "sign",
            env=env,
        )

    dbpath = artifacts_dir / "rpmdb/632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"
    assert dbpath.exists()

    rpms = [
        "qubes-core-qrexec-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
    ]
    for rpm in rpms:
        rpm_path = (
            artifacts_dir
            / f"components/core-qrexec/4.2.18-1/host-fc37/{f'prep/{rpm}' if rpm.endswith('.src.rpm') else f'build/rpm/{rpm}'}"
        )
        assert rpm_path.exists()
        result = subprocess.run(
            f"rpm --dbpath {dbpath} -K {rpm_path}",
            check=True,
            capture_output=True,
            shell=True,
        )
        assert "digests signatures OK" in result.stdout.decode()

    # Ensure that original content is at least here with the 3 headers PGP BEGIN/END and at least
    # one line inside the signature
    signed_buildinfo_number_lines = len(
        buildinfo.read_text(encoding="utf8").splitlines()
    )
    assert signed_buildinfo_number_lines > buildinfo_number_lines + 4


def test_component_host_fc37_publish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into unstable
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        rpms = {
            "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        }
        srpm = "qubes-core-qrexec-4.2.18-1.fc37.src.rpm"

        rpms_dom0 = {
            "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
        }
        srpm_dom0 = "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm"

        with open(
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec.spec.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        with open(
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec-dom0.spec.publish.yml"
        ) as f:
            info_dom0 = yaml.safe_load(f.read())

        assert set(info.get("rpms", [])) == rpms
        assert info.get("srpm", None) == srpm
        assert HASH_RE.match(info.get("source-hash", None))
        assert ["unstable"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert ["unstable"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into current-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )
        with open(
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec.spec.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        with open(
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec-dom0.spec.publish.yml"
        ) as f:
            info_dom0 = yaml.safe_load(f.read())

        assert set(info.get("rpms", [])) == rpms
        assert info.get("srpm", None) == srpm
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        # buildinfo
        assert (
            artifacts_dir
            / "repository-publish/rpm/r4.2/current-testing/host/fc37"
        ).exists()

        # publish into current
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")
        publish_file = (
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec.spec.publish.yml"
        )
        publish_dom0_file = (
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec-dom0.spec.publish.yml"
        )

        for r in info["repository-publish"]:
            if r["name"] == "current-testing":
                r["timestamp"] = fake_time
                break

        for r in info_dom0["repository-publish"]:
            if r["name"] == "current-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_file, "w") as f:
            f.write(yaml.safe_dump(info))

        with open(publish_dom0_file, "w") as f:
            f.write(yaml.safe_dump(info_dom0))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "repository",
            "publish",
            "current",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        with open(publish_dom0_file) as f:
            info_dom0 = yaml.safe_load(f.read())

        assert set(info.get("rpms", [])) == rpms
        assert info.get("srpm", None) == srpm
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
            "current",
        }

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
            "current",
        }

        metadata_dir = (
            artifacts_dir
            / f"repository-publish/rpm/r4.2/current/host/fc37/repodata"
        )
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )
        assert (
            "/pub/os/qubes/repo/yum/r4.2/current/host/fc37/repodata/repomd.xml"
            in (metadata_dir / "repomd.xml.metalink").read_text(
                encoding="ascii"
            )
        )

        # buildinfo
        assert (
            artifacts_dir / "repository-publish/rpm/r4.2/current/host/fc37"
        ).exists()

    rpms = {
        "qubes-core-qrexec-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
    }

    # Check that packages are in the published repository
    for repository in ["unstable", "current-testing", "current"]:
        repository_dir = f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}/host/fc37"
        packages = rpm_packages_list(repository_dir)
        assert rpms == set(packages)


def test_component_host_fc37_upload(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        builder_conf = tmpdir + "/builder.yml"
        with open(builder_conf, "w") as builder_f:
            builder_f.write(DEFAULT_BUILDER_CONF.read_text())
            builder_f.write(
                f"""
repository-upload-remote-host:
 rpm: {tmpdir}/repo/rpm/r4.2
 deb: {tmpdir}/repo/deb/r4.2
"""
            )

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # upload into unstable, only host-fc37
        qb_call(
            builder_conf,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "repository",
            "upload",
            "unstable",
            env=env,
        )

        rpms = {
            "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        }
        srpm = "qubes-core-qrexec-4.2.18-1.fc37.src.rpm"

        rpms_dom0 = {
            "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
        }
        srpm_dom0 = "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm"

        for rpm in itertools.chain([srpm_dom0], rpms_dom0, rpms, [srpm]):
            assert (
                pathlib.Path(tmpdir)
                / f"repo/rpm/r4.2/unstable/host/fc37/rpm/{rpm}"
            ).exists()

        # vm-fc40 shouldn't exist, as nothing was published into it
        assert not (
            pathlib.Path(tmpdir) / f"repo/rpm/r4.2/unstable/vm/fc40"
        ).exists()

        # and vm-bookworm same
        assert not (
            pathlib.Path(tmpdir) / f"repo/deb/r4.2/vm/dists/bookworm-unstable"
        ).exists()


# Check that we properly ignore already done stages.


def test_component_host_fc37_prep_skip(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "core-qrexec",
        "-d",
        "host-fc37",
        "package",
        "prep",
    ).decode()
    print(result)
    assert (
        "core-qrexec:host-fedora-37.x86_64: Source hash is the same than already prepared source. Skipping."
        in result
    )


def test_component_host_fc37_build_skip(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "core-qrexec",
        "-d",
        "host-fc37",
        "package",
        "build",
    ).decode()
    print(result)
    assert (
        "core-qrexec:host-fedora-37.x86_64: Source hash is the same than already built source. Skipping."
        in result
    )


def test_component_host_fc37_sign_skip(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        result = qb_call_output(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "package",
            "sign",
            env=env,
        ).decode()

    rpms = [
        "qubes-core-qrexec-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
    ]
    for rpm in rpms:
        assert f"{rpm} has already a valid signature. Skipping." in result


# Check that we unpublish properly from current-testing


def test_component_host_fc37_unpublish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into unstable
        result = qb_call_output(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "-o",
            "automatic-upload-on-publish=true",
            "repository",
            "unpublish",
            "current",
            env=env,
        ).decode()

        rpms = {
            "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        }
        srpm = "qubes-core-qrexec-4.2.18-1.fc37.src.rpm"

        rpms_dom0 = {
            "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
            "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
        }
        srpm_dom0 = "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm"

        with open(
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec.spec.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        with open(
            artifacts_dir
            / "components/core-qrexec/4.2.18-1/host-fc37/publish/rpm_spec_qubes-qrexec-dom0.spec.publish.yml"
        ) as f:
            info_dom0 = yaml.safe_load(f.read())

        assert set(info.get("rpms", [])) == rpms
        assert info.get("srpm", None) == srpm
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert set(
            [r["name"] for r in info_dom0.get("repository-publish", [])]
        ) == {
            "unstable",
            "current-testing",
        }

    # Check that packages are in the published repository
    rpms = {
        "qubes-core-qrexec-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.src.rpm",
        "qubes-core-qrexec-dom0-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.fc37.x86_64.rpm",
    }
    for repository in ["unstable", "current-testing", "current"]:
        repository_dir = f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}/host/fc37"
        packages = rpm_packages_list(repository_dir)
        if repository == "current":
            assert packages == []
        else:
            assert rpms == set(packages)

    assert "[qb.publish_rpm.core-qrexec.host-fc37]" in result
    assert "[qb.upload.host-fc37]" in result


#
# Pipeline for python-qasync and vm-bookworm
#


def test_component_vm_bookworm_init_cache(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-bookworm",
        "package",
        "init-cache",
    )
    assert (artifacts_dir / "cache/chroot/bookworm/pbuilder/base.tgz").exists()


def test_component_vm_bookworm_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "python-qasync",
        "-d",
        "vm-bookworm",
        "package",
        "fetch",
        "prep",
    )

    with open(
        artifacts_dir
        / "components/python-qasync/0.23.0-2/vm-bookworm/prep/debian-pkg_debian.prep.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    packages = [
        "python3-qasync_0.23.0-2+deb12u1_all.deb",
        "python3-qasync-dbgsym_0.23.0-2+deb12u1_all.deb",
        "python3-qasync-dbgsym_0.23.0-2+deb12u1_all.ddeb",
    ]
    debian = "python-qasync_0.23.0-2+deb12u1.debian.tar.xz"
    dsc = "python-qasync_0.23.0-2+deb12u1.dsc"
    orig = "python-qasync_0.23.0.orig.tar.gz"
    package_release_name = "python-qasync_0.23.0"
    package_release_name_full = "python-qasync_0.23.0-2+deb12u1"

    assert info.get("packages", []) == packages
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("debian", None) == debian
    assert info.get("dsc", None) == dsc
    assert info.get("orig", None) == orig
    assert info.get("package-release-name", None) == package_release_name
    assert (
        info.get("package-release-name-full", None) == package_release_name_full
    )


def test_component_vm_bookworm_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "python-qasync",
        "-d",
        "vm-bookworm",
        "package",
        "build",
    )

    with open(
        artifacts_dir
        / "components/python-qasync/0.23.0-2/vm-bookworm/build/debian-pkg_debian.build.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    packages = ["python3-qasync_0.23.0-2+deb12u1_all.deb"]
    debian = "python-qasync_0.23.0-2+deb12u1.debian.tar.xz"
    dsc = "python-qasync_0.23.0-2+deb12u1.dsc"
    orig = "python-qasync_0.23.0.orig.tar.gz"
    package_release_name = "python-qasync_0.23.0"
    package_release_name_full = "python-qasync_0.23.0-2+deb12u1"

    assert info.get("packages", []) == packages
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("debian", None) == debian
    assert info.get("dsc", None) == dsc
    assert info.get("orig", None) == orig
    assert info.get("package-release-name", None) == package_release_name
    assert (
        info.get("package-release-name-full", None) == package_release_name_full
    )

    files = [
        "python-qasync_0.23.0-2+deb12u1.dsc",
        "python-qasync_0.23.0-2+deb12u1_amd64.changes",
        "python-qasync_0.23.0-2+deb12u1_amd64.buildinfo",
    ]
    for file in files:
        file_path = (
            artifacts_dir
            / f"components/python-qasync/0.23.0-2/vm-bookworm/build/{file}"
        )
        assert file_path.exists()
        result = subprocess.run(
            f"dscverify --no-sig-check {file_path}",
            shell=True,
        )
        assert result.returncode == 0


def test_component_vm_bookworm_sign(artifacts_dir):
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
            "python-qasync",
            "-d",
            "vm-bookworm",
            "package",
            "sign",
            env=env,
        )

    keyring_dir = (
        artifacts_dir
        / "components/python-qasync/0.23.0-2/vm-bookworm/sign/keyring"
    )
    assert keyring_dir.exists()

    files = [
        "python-qasync_0.23.0-2+deb12u1.dsc",
        "python-qasync_0.23.0-2+deb12u1_amd64.changes",
        "python-qasync_0.23.0-2+deb12u1_amd64.buildinfo",
    ]
    for f in files:
        file_path = (
            artifacts_dir
            / f"components/python-qasync/0.23.0-2/vm-bookworm/build/{f}"
        )
        assert file_path.exists()
        result = subprocess.run(
            f"gpg2 --homedir {keyring_dir} --verify {file_path}",
            shell=True,
        )
        assert result.returncode == 0
        result = subprocess.run(
            f"dscverify --no-sig-check {file_path}",
            shell=True,
        )
        assert result.returncode == 0


def test_component_vm_bookworm_publish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into unstable
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "python-qasync",
            "-d",
            "vm-bookworm",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        with open(
            artifacts_dir
            / "components/python-qasync/0.23.0-2/vm-bookworm/publish/debian-pkg_debian.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        packages = ["python3-qasync_0.23.0-2+deb12u1_all.deb"]
        debian = "python-qasync_0.23.0-2+deb12u1.debian.tar.xz"
        dsc = "python-qasync_0.23.0-2+deb12u1.dsc"
        orig = "python-qasync_0.23.0.orig.tar.gz"
        package_release_name = "python-qasync_0.23.0"
        package_release_name_full = "python-qasync_0.23.0-2+deb12u1"

        assert info.get("packages", []) == packages
        assert HASH_RE.match(info.get("source-hash", None))
        assert info.get("debian", None) == debian
        assert info.get("dsc", None) == dsc
        assert info.get("orig", None) == orig
        assert info.get("package-release-name", None) == package_release_name
        assert (
            info.get("package-release-name-full", None)
            == package_release_name_full
        )
        assert ["unstable"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into current-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "python-qasync",
            "-d",
            "vm-bookworm",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )
        with open(
            artifacts_dir
            / "components/python-qasync/0.23.0-2/vm-bookworm/publish/debian-pkg_debian.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        assert info.get("packages", []) == packages
        assert HASH_RE.match(info.get("source-hash", None))
        assert info.get("debian", None) == debian
        assert info.get("dsc", None) == dsc
        assert info.get("orig", None) == orig
        assert info.get("package-release-name", None) == package_release_name
        assert (
            info.get("package-release-name-full", None)
            == package_release_name_full
        )
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        # publish into current
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")
        publish_file = (
            artifacts_dir
            / "components/python-qasync/0.23.0-2/vm-bookworm/publish/debian-pkg_debian.publish.yml"
        )

        for r in info["repository-publish"]:
            if r["name"] == "current-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_file, "w") as f:
            f.write(yaml.safe_dump(info))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "python-qasync",
            "-d",
            "vm-bookworm",
            "repository",
            "publish",
            "current",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("packages", []) == packages
        assert HASH_RE.match(info.get("source-hash", None))
        assert info.get("debian", None) == debian
        assert info.get("dsc", None) == dsc
        assert info.get("orig", None) == orig
        assert info.get("package-release-name", None) == package_release_name
        assert (
            info.get("package-release-name-full", None)
            == package_release_name_full
        )
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
            "current",
        }

    # Check that packages are in the published repositories
    repository_dir = artifacts_dir / "repository-publish/deb/r4.2/vm"
    for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
        packages = deb_packages_list(repository_dir, codename)
        expected_packages = [
            f"{codename}|main|amd64: python3-qasync 0.23.0-2+deb12u1",
            f"{codename}|main|source: python-qasync 0.23.0-2+deb12u1",
        ]
        assert set(packages) == set(expected_packages)
        # verify if repository is signed
        assert (repository_dir / "dists" / codename / "InRelease").exists()
        assert (repository_dir / "dists" / codename / "Release.gpg").exists()


# Check that we properly ignore already done stages.


def test_component_vm_bookworm_prep_skip(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "python-qasync",
        "-d",
        "vm-bookworm",
        "package",
        "prep",
    ).decode()
    print(result)
    assert (
        "python-qasync:vm-debian-12.amd64: Source hash is the same than already prepared source. Skipping."
        in result
    )


def test_component_vm_bookworm_build_skip(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "python-qasync",
        "-d",
        "vm-bookworm",
        "package",
        "build",
    ).decode()
    print(result)
    assert (
        "python-qasync:vm-debian-12.amd64: Source hash is the same than already built source. Skipping."
        in result
    )


def test_component_vm_bookworm_sign_skip(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        result = qb_call_output(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "python-qasync",
            "-d",
            "vm-bookworm",
            "package",
            "sign",
            env=env,
        ).decode()

    assert f"Leaving current signature unchanged." in result


# Check that we unpublish properly from current-testing


def test_component_vm_bookworm_unpublish(artifacts_dir):
    # FIXME: we rely on previous test_publish_vm_bookworm being ran before
    repository_dir = artifacts_dir / "repository-publish/deb/r4.2/vm"
    for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
        packages = deb_packages_list(repository_dir, codename)
        expected_packages = [
            f"{codename}|main|amd64: python3-qasync 0.23.0-2+deb12u1",
            f"{codename}|main|source: python-qasync 0.23.0-2+deb12u1",
        ]
        assert set(packages) == set(expected_packages)

    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into unstable
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "python-qasync",
            "-d",
            "vm-bookworm",
            "repository",
            "unpublish",
            "current",
            env=env,
        )

        packages = ["python3-qasync_0.23.0-2+deb12u1_all.deb"]
        debian = "python-qasync_0.23.0-2+deb12u1.debian.tar.xz"
        dsc = "python-qasync_0.23.0-2+deb12u1.dsc"
        orig = "python-qasync_0.23.0.orig.tar.gz"
        package_release_name = "python-qasync_0.23.0"
        package_release_name_full = "python-qasync_0.23.0-2+deb12u1"

        with open(
            artifacts_dir
            / "components/python-qasync/0.23.0-2/vm-bookworm/publish/debian-pkg_debian.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        assert info.get("packages", []) == packages
        assert HASH_RE.match(info.get("source-hash", None))
        assert info.get("debian", None) == debian
        assert info.get("dsc", None) == dsc
        assert info.get("orig", None) == orig
        assert info.get("package-release-name", None) == package_release_name
        assert (
            info.get("package-release-name-full", None)
            == package_release_name_full
        )
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

    # Check that packages are in the published repositories
    repository_dir = artifacts_dir / "repository-publish/deb/r4.2/vm"
    for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
        packages = deb_packages_list(repository_dir, codename)
        if codename == "bookworm":
            expected_packages = []
        else:
            expected_packages = [
                f"{codename}|main|amd64: python3-qasync 0.23.0-2+deb12u1",
                f"{codename}|main|source: python-qasync 0.23.0-2+deb12u1",
            ]
        assert set(packages) == set(expected_packages)


def test_increment_component_fetch(artifacts_dir):
    # # clean
    # for d in ["sources", "components", "repository", "repository-publish", "tmp"]:
    #     if not (artifacts_dir / d).exists():
    #         continue
    #     shutil.rmtree(artifacts_dir / d)

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "increment-devel-versions=true",
        "package",
        "fetch",
    )

    for component in [
        "core-qrexec",
        "core-vchan-xen",
        "desktop-linux-xfce4-xfwm4",
        "python-qasync",
    ]:
        assert (
            artifacts_dir / "components" / component / "noversion" / "devel"
        ).exists()

        (artifacts_dir / "sources" / component / "hello").write_text(
            "world", encoding="utf8"
        )

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "increment-devel-versions=true",
        "package",
        "fetch",
    )

    for component in [
        "core-qrexec",
        "core-vchan-xen",
        "desktop-linux-xfce4-xfwm4",
        "python-qasync",
    ]:
        assert (
            artifacts_dir / "components" / component / "noversion" / "devel"
        ).read_text(encoding="utf-8") == "2"


def test_increment_component_build(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        devel_path = (
            pathlib.Path(artifacts_dir)
            / "components/core-qrexec/noversion/devel"
        )
        devel_path.write_text("41", encoding="utf-8")

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "--option",
            "increment-devel-versions=true",
            "package",
            "fetch",
        )

        assert (
            artifacts_dir / "components/core-qrexec/noversion/devel"
        ).read_text(encoding="utf-8") == "42"

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "core-qrexec",
            "-d",
            "host-fc37",
            "-d",
            "vm-bookworm",
            "package",
            "all",
            env=env,
        )

    rpms = {
        "qubes-core-qrexec-4.2.18-1.42.fc37.x86_64.rpm",
        "qubes-core-qrexec-debugsource-4.2.18-1.42.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-4.2.18-1.42.fc37.x86_64.rpm",
        "qubes-core-qrexec-libs-debuginfo-4.2.18-1.42.fc37.x86_64.rpm",
        "qubes-core-qrexec-devel-4.2.18-1.42.fc37.x86_64.rpm",
    }
    srpm = "qubes-core-qrexec-4.2.18-1.42.fc37.src.rpm"

    for rpm in rpms.union({srpm}):
        assert (
            artifacts_dir / "repository/host-fc37/core-qrexec_4.2.18" / rpm
        ).exists()

    rpms = {
        "qubes-core-qrexec-dom0-4.2.18-1.42.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debuginfo-4.2.18-1.42.fc37.x86_64.rpm",
        "qubes-core-qrexec-dom0-debugsource-4.2.18-1.42.fc37.x86_64.rpm",
    }
    srpm = "qubes-core-qrexec-dom0-4.2.18-1.42.fc37.src.rpm"

    for rpm in rpms.union({srpm}):
        assert (
            artifacts_dir / "repository/host-fc37/core-qrexec_4.2.18" / rpm
        ).exists()

    deb_files = [
        "libqrexec-utils-dev_4.2.18-1+deb12u1+devel42_amd64.deb",
        "libqrexec-utils2-dbgsym_4.2.18-1+deb12u1+devel42_amd64.deb",
        "libqrexec-utils2_4.2.18-1+deb12u1+devel42_amd64.deb",
        "python3-qrexec_4.2.18-1+deb12u1+devel42_amd64.deb",
        "qubes-core-qrexec-dbgsym_4.2.18-1+deb12u1+devel42_amd64.deb",
        "qubes-core-qrexec_4.2.18-1+deb12u1+devel42.debian.tar.xz",
        "qubes-core-qrexec_4.2.18-1+deb12u1+devel42.dsc",
        "qubes-core-qrexec_4.2.18-1+deb12u1+devel42_amd64.buildinfo",
        "qubes-core-qrexec_4.2.18-1+deb12u1+devel42_amd64.changes",
        "qubes-core-qrexec_4.2.18-1+deb12u1+devel42_amd64.deb",
        "qubes-core-qrexec_4.2.18.orig.tar.gz",
    ]

    for file in deb_files:
        assert (
            artifacts_dir / "repository/vm-bookworm/core-qrexec_4.2.18" / file
        ).exists()


#
# Pipeline for app-linux-split-gpg and vm-archlinux
#


def test_component_vm_archlinux_init_cache(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-archlinux",
        "package",
        "init-cache",
    )
    assert (artifacts_dir / "cache/chroot/archlinux/root.tar.gz").exists()


def test_component_vm_archlinux_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "app-linux-split-gpg",
        "-d",
        "vm-archlinux",
        "package",
        "fetch",
        "prep",
    )

    with open(
        artifacts_dir
        / "components/app-linux-split-gpg/2.0.67-1/vm-archlinux/prep/archlinux.prep.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    assert info.get("packages", []) == [
        "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
    ]
    assert info.get("source-archive", None) == "qubes-gpg-split-2.0.67-1.tar.gz"
    assert HASH_RE.match(info.get("source-hash", None))


def test_component_vm_archlinux_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "app-linux-split-gpg",
        "-d",
        "vm-archlinux",
        "package",
        "build",
    )

    with open(
        artifacts_dir
        / "components/app-linux-split-gpg/2.0.67-1/vm-archlinux/build/archlinux.build.yml"
    ) as f:
        info = yaml.safe_load(f.read())

    assert info.get("packages", []) == [
        "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
    ]
    assert info.get("source-archive", None) == "qubes-gpg-split-2.0.67-1.tar.gz"
    assert HASH_RE.match(info.get("source-hash", None))

    pkg_path = (
        artifacts_dir
        / f"components/app-linux-split-gpg/2.0.67-1/vm-archlinux/build/pkgs/qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
    )
    assert pkg_path.exists()


def test_component_vm_archlinux_sign(artifacts_dir):
    env = os.environ.copy()

    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        # Better copy testing keyring into a separate directory to prevent locks inside
        # local sources (when executed locally).
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        # Enforce keyring location
        env["GNUPGHOME"] = gnupghome
        # We prevent rpm to find ~/.rpmmacros
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "app-linux-split-gpg",
            "-d",
            "vm-archlinux",
            "package",
            "sign",
            env=env,
        )
        pkgs_dir = (
            artifacts_dir
            / "components/app-linux-split-gpg/2.0.67-1/vm-archlinux/build/pkgs"
        )
        pkg_path = pkgs_dir / "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
        pkg_sig_path = (
            pkgs_dir / "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst.sig"
        )
        assert pkg_path.exists()
        assert pkg_sig_path.exists()

        subprocess.run(
            f"gpg2 -q --verify {pkg_sig_path} {pkg_path}",
            check=True,
            capture_output=True,
            shell=True,
            env=env,
        )


def test_component_vm_archlinux_publish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into unstable
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "app-linux-split-gpg",
            "-d",
            "vm-archlinux",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        with open(
            artifacts_dir
            / "components/app-linux-split-gpg/2.0.67-1/vm-archlinux/publish/archlinux.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        assert info.get("packages", []) == [
            "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
        ]
        assert HASH_RE.match(info.get("source-hash", None))
        assert ["unstable"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into current-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "app-linux-split-gpg",
            "-d",
            "vm-archlinux",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )
        with open(
            artifacts_dir
            / "components/app-linux-split-gpg/2.0.67-1/vm-archlinux/publish/archlinux.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        assert info.get("packages", []) == [
            "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
        ]
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        # publish into current
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")
        publish_file = (
            artifacts_dir
            / "components/app-linux-split-gpg/2.0.67-1/vm-archlinux/publish/archlinux.publish.yml"
        )

        for r in info["repository-publish"]:
            if r["name"] == "current-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_file, "w") as f:
            f.write(yaml.safe_dump(info))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "app-linux-split-gpg",
            "-d",
            "vm-archlinux",
            "repository",
            "publish",
            "current",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("packages", []) == [
            "qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
        ]
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
            "current",
        }

        qubesdb = (
            artifacts_dir
            / f"repository-publish/archlinux/r4.2/current/vm/archlinux/pkgs/qubes-r4.2-current.db.tar.gz"
        )
        assert qubesdb.exists()

        qubesdb_sig = (
            artifacts_dir
            / f"repository-publish/archlinux/r4.2/current/vm/archlinux/pkgs/qubes-r4.2-current.db.tar.gz.sig"
        )
        assert qubesdb_sig.exists()


def test_component_vm_archlinux_upload(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        builder_conf = tmpdir + "/builder.yml"
        with open(builder_conf, "w") as builder_f:
            builder_f.write(DEFAULT_BUILDER_CONF.read_text())
            builder_f.write(
                f"""
repository-upload-remote-host:
 rpm: {tmpdir}/repo/rpm/r4.2
 deb: {tmpdir}/repo/deb/r4.2
 archlinux: {tmpdir}/repo/archlinux/r4.2
"""
            )

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # upload into unstable, only vm-archlinux
        qb_call(
            builder_conf,
            artifacts_dir,
            "-c",
            "app-linux-split-gpg",
            "-d",
            "vm-archlinux",
            "repository",
            "upload",
            "unstable",
            env=env,
        )

        assert (
            pathlib.Path(tmpdir)
            / f"repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst"
        ).exists()

        assert (
            pathlib.Path(tmpdir)
            / f"repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/qubes-gpg-split-2.0.67-1-x86_64.pkg.tar.zst.sig"
        ).exists()

        assert (
            pathlib.Path(tmpdir)
            / f"repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/qubes-r4.2-unstable.db.tar.gz"
        ).exists()

        assert (
            pathlib.Path(tmpdir)
            / f"repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/qubes-r4.2-unstable.db.tar.gz.sig"
        ).exists()

        # vm-fc40 shouldn't exist, as nothing was published into it
        assert not (
            pathlib.Path(tmpdir) / f"repo/rpm/r4.2/unstable/vm/fc40"
        ).exists()

        # and vm-bookworm same
        assert not (
            pathlib.Path(tmpdir) / f"repo/deb/r4.2/vm/dists/bookworm-unstable"
        ).exists()


def _get_template_timestamp(artifacts_dir, template_name, stage):
    assert (artifacts_dir / f"templates/{template_name}.{stage}.yml").exists()
    with open(artifacts_dir / f"templates/{template_name}.{stage}.yml") as f:
        data = yaml.safe_load(f.read())
    assert data.get("timestamp", None)
    return parsedate(data["timestamp"]).strftime("%Y%m%d%H%M")


#
# Pipeline for Fedora 40 template
#


def test_template_fedora_40_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "qubes-release",
        "-c",
        "builder-rpm",
        "package",
        "fetch",
    )

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-t",
        "fedora-40-minimal",
        "template",
        "prep",
    )

    assert (artifacts_dir / "templates/fedora-40-minimal.prep.yml").exists()
    assert (
        artifacts_dir / "templates/qubeized_images/fedora-40-minimal/root.img"
    ).exists()
    assert (artifacts_dir / "templates/fedora-40-minimal/appmenus").exists()
    assert (
        artifacts_dir / "templates/fedora-40-minimal/template.conf"
    ).exists()


def test_template_fedora_40_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-t",
        "fedora-40-minimal",
        "template",
        "build",
    )

    template_prep_timestamp = _get_template_timestamp(
        artifacts_dir, "fedora-40-minimal", "prep"
    )

    template_timestamp = _get_template_timestamp(
        artifacts_dir, "fedora-40-minimal", "build"
    )

    assert template_timestamp == template_prep_timestamp

    rpm_path = (
        artifacts_dir
        / f"templates/rpm/qubes-template-fedora-40-minimal-4.2.0-{template_timestamp}.noarch.rpm"
    )
    assert rpm_path.exists()


def test_template_fedora_40_minimal_sign(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        # Better copy testing keyring into a separate directory to prevent locks inside
        # local sources (when executed locally).
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        # Enforce keyring location
        env["GNUPGHOME"] = gnupghome
        # We prevent rpm to find ~/.rpmmacros
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "template",
            "sign",
            env=env,
        )

    dbpath = artifacts_dir / "templates/rpmdb"
    assert dbpath.exists()

    template_timestamp = _get_template_timestamp(
        artifacts_dir, "fedora-40-minimal", "build"
    )

    rpm_path = (
        artifacts_dir
        / f"templates/rpm/qubes-template-fedora-40-minimal-4.2.0-{template_timestamp}.noarch.rpm"
    )
    assert rpm_path.exists()
    result = subprocess.run(
        f"rpm --dbpath {dbpath} -K {rpm_path}",
        check=True,
        capture_output=True,
        shell=True,
    )
    assert "digests signatures OK" in result.stdout.decode()


def test_template_fedora_40_minimal_publish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into templates-itl-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "repository",
            "publish",
            "templates-itl-testing",
            env=env,
        )

        with open(
            artifacts_dir / "templates/fedora-40-minimal.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        template_timestamp = _get_template_timestamp(
            artifacts_dir, "fedora-40-minimal", "build"
        )

        assert info.get("timestamp", []) == template_timestamp
        assert ["templates-itl-testing"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into templates-itl
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")
        publish_file = artifacts_dir / "templates/fedora-40-minimal.publish.yml"

        for r in info["repository-publish"]:
            if r["name"] == "templates-itl-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_file, "w") as f:
            f.write(yaml.safe_dump(info))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "repository",
            "publish",
            "templates-itl",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("timestamp", []) == template_timestamp
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "templates-itl-testing",
            "templates-itl",
        }

    # Check that packages are in the published repository
    for repository in ["templates-itl-testing", "templates-itl"]:
        rpm = f"qubes-template-fedora-40-minimal-4.2.0-{template_timestamp}.noarch.rpm"
        repository_dir = (
            f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}"
        )
        packages = rpm_packages_list(repository_dir)
        assert {rpm} == set(packages)
        metadata_dir = (
            artifacts_dir / f"repository-publish/rpm/r4.2/{repository}/repodata"
        )
        assert (metadata_dir / "repomd.xml").exists()
        assert (metadata_dir / "repomd.xml.asc").exists()
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )


# @pytest.mark.depends(on=['test_template_publish_fedora_40_minimal'])
def test_template_fedora_40_minimal_publish_new(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        assert (
            artifacts_dir / "templates/fedora-40-minimal.build.yml"
        ).exists()
        with open(artifacts_dir / "templates/fedora-40-minimal.build.yml") as f:
            data = yaml.safe_load(f.read())
        assert data.get("timestamp", None)
        template_timestamp = parsedate(data["timestamp"]).strftime("%Y%m%d%H%M")

        # bump timestamp, without re-running "prep" stage
        new_timestamp = (
            parsedate(data["timestamp"]) + datetime.timedelta(minutes=1)
        ).strftime("%Y%m%d%H%M")
        data["timestamp"] = new_timestamp
        with open(
            artifacts_dir / "templates/fedora-40-minimal.prep.yml", "w"
        ) as f:
            f.write(yaml.dump(data))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "template",
            "build",
            "sign",
            env=env,
        )

        rpm_path = (
            artifacts_dir
            / f"templates/rpm/qubes-template-fedora-40-minimal-4.2.0-{new_timestamp}.noarch.rpm"
        )
        assert rpm_path.exists()

        assert template_timestamp != new_timestamp

        # publish into templates-itl-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "repository",
            "publish",
            "templates-itl-testing",
            env=env,
        )

        publish_file = artifacts_dir / "templates/fedora-40-minimal.publish.yml"
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("timestamp", []) == new_timestamp
        assert ["templates-itl-testing"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into templates-itl
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")

        # pretend it was in testing repo for 7 days already
        for r in info["repository-publish"]:
            if r["name"] == "templates-itl-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_file, "w") as f:
            f.write(yaml.safe_dump(info))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "repository",
            "publish",
            "templates-itl",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("timestamp", []) == new_timestamp
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "templates-itl-testing",
            "templates-itl",
        }

    # Check that packages are in the published repository
    for repository in ["templates-itl-testing", "templates-itl"]:
        rpms = {
            f"qubes-template-fedora-40-minimal-4.2.0-{template_timestamp}.noarch.rpm",
            f"qubes-template-fedora-40-minimal-4.2.0-{new_timestamp}.noarch.rpm",
        }
        repository_dir = (
            f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}"
        )
        packages = rpm_packages_list(repository_dir)
        assert rpms == set(packages)
        metadata_dir = (
            artifacts_dir / f"repository-publish/rpm/r4.2/{repository}/repodata"
        )
        assert (metadata_dir / "repomd.xml").exists()
        assert (metadata_dir / "repomd.xml.asc").exists()
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )


def test_template_fedora_40_minimal_unpublish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        template_timestamp = _get_template_timestamp(
            artifacts_dir, "fedora-40-minimal", "build"
        )

        # unpublish from templates-itl
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "repository",
            "unpublish",
            "templates-itl",
            env=env,
        )

        publish_file = artifacts_dir / "templates/fedora-40-minimal.publish.yml"
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("timestamp", []) == template_timestamp
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "templates-itl-testing"
        }

    # Check that packages are in the published repository
    for repository in ["templates-itl-testing", "templates-itl"]:
        rpm = f"qubes-template-fedora-40-minimal-4.2.0-{template_timestamp}.noarch.rpm"
        repository_dir = (
            f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}"
        )
        packages = rpm_packages_list(repository_dir)
        if repository == "templates-itl":
            assert rpm not in packages
        else:
            assert rpm in packages
        metadata_dir = (
            artifacts_dir / f"repository-publish/rpm/r4.2/{repository}/repodata"
        )
        assert (metadata_dir / "repomd.xml").exists()
        assert (metadata_dir / "repomd.xml.asc").exists()
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )


#
# Pipeline for providing template to ISO build
#


def test_template_fedora_for_iso(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        template_timestamp = _get_template_timestamp(
            artifacts_dir, "fedora-40-minimal", "build"
        )
        rpm = f"qubes-template-fedora-40-minimal-4.2.0-{template_timestamp}.noarch.rpm"

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "qubes-release",
            "package",
            "fetch",
        )

        default_kickstart = (
            artifacts_dir
            / "sources/qubes-release/conf/iso-online-testing-no-templates.ks"
        )

        kickstart = tmpdir + "/tests-kickstart.cfg"
        with open(kickstart, "w") as kickstart_f:
            kickstart_f.write(default_kickstart.read_text())
            kickstart_f.write(
                """
%packages
qubes-template-fedora-40-minimal
%end
"""
            )

        # make ISO cache with a single template
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "fedora-40-minimal",
            "-o",
            f"iso:kickstart={kickstart!s}",
            "installer",
            "init-cache",
            "prep",
            env=env,
        )

        with open(artifacts_dir / "installer/latest_fc37_iso_timestamp") as f:
            data = f.read().splitlines()
        latest_iso_timestamp = parsedate(data[0]).strftime("%Y%m%d%H%M")

        installer_iso_pkgs = (
            artifacts_dir
            / "cache"
            / "installer"
            / f"Qubes-{latest_iso_timestamp}-x86_64"
            / "work"
            / latest_iso_timestamp
            / "x86_64/os/Packages"
        )
        assert (installer_iso_pkgs / rpm).exists()


#
# Pipeline for Debian 12 minimal template
#


def test_template_debian_12_minimal_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "builder-debian",
        "package",
        "fetch",
    )

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-t",
        "debian-12-minimal",
        "template",
        "prep",
    )

    assert (artifacts_dir / "templates/debian-12-minimal.prep.yml").exists()
    assert (
        artifacts_dir / "templates/qubeized_images/debian-12-minimal/root.img"
    ).exists()
    assert (artifacts_dir / "templates/debian-12-minimal/appmenus").exists()
    assert (
        artifacts_dir / "templates/debian-12-minimal/template.conf"
    ).exists()


def test_template_debian_12_minimal_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-t",
        "debian-12-minimal",
        "template",
        "build",
    )

    template_prep_timestamp = _get_template_timestamp(
        artifacts_dir, "debian-12-minimal", "prep"
    )

    template_timestamp = _get_template_timestamp(
        artifacts_dir, "debian-12-minimal", "build"
    )

    assert template_timestamp == template_prep_timestamp
    rpm_path = (
        artifacts_dir
        / f"templates/rpm/qubes-template-debian-12-minimal-4.2.0-{template_timestamp}.noarch.rpm"
    )
    assert rpm_path.exists()


def test_template_debian_12_minimal_sign(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        # Better copy testing keyring into a separate directory to prevent locks inside
        # local sources (when executed locally).
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        # Enforce keyring location
        env["GNUPGHOME"] = gnupghome
        # We prevent rpm to find ~/.rpmmacros
        env["HOME"] = tmpdir

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "debian-12-minimal",
            "template",
            "sign",
            env=env,
        )

    dbpath = artifacts_dir / "templates/rpmdb"
    assert dbpath.exists()

    template_timestamp = _get_template_timestamp(
        artifacts_dir, "debian-12-minimal", "build"
    )

    rpm_path = (
        artifacts_dir
        / f"templates/rpm/qubes-template-debian-12-minimal-4.2.0-{template_timestamp}.noarch.rpm"
    )
    assert rpm_path.exists()
    result = subprocess.run(
        f"rpm --dbpath {dbpath} -K {rpm_path}",
        check=True,
        capture_output=True,
        shell=True,
    )
    assert "digests signatures OK" in result.stdout.decode()


def test_template_debian_12_minimal_publish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # publish into templates-community-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "debian-12-minimal",
            "repository",
            "publish",
            "templates-community-testing",
            env=env,
        )

        with open(
            artifacts_dir / "templates/debian-12-minimal.publish.yml"
        ) as f:
            info = yaml.safe_load(f.read())

        template_timestamp = _get_template_timestamp(
            artifacts_dir, "debian-12-minimal", "build"
        )

        assert info.get("timestamp", []) == template_timestamp
        assert ["templates-community-testing"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into templates-community
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")
        publish_file = artifacts_dir / "templates/debian-12-minimal.publish.yml"

        for r in info["repository-publish"]:
            if r["name"] == "templates-community-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_file, "w") as f:
            f.write(yaml.safe_dump(info))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            "debian-12-minimal",
            "repository",
            "publish",
            "templates-community",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("timestamp", []) == template_timestamp
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "templates-community-testing",
            "templates-community",
        }

    # Check that packages are in the published repository
    for repository in ["templates-community-testing", "templates-community"]:
        rpm = f"qubes-template-debian-12-minimal-4.2.0-{template_timestamp}.noarch.rpm"
        repository_dir = (
            f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}"
        )
        packages = rpm_packages_list(repository_dir)
        assert {rpm} == set(packages)
        metadata_dir = (
            artifacts_dir / f"repository-publish/rpm/r4.2/{repository}/repodata"
        )
        assert (metadata_dir / "repomd.xml").exists()
        assert (metadata_dir / "repomd.xml.asc").exists()
        assert (metadata_dir / "repomd.xml.metalink").exists()
        with open((metadata_dir / "repomd.xml"), "rb") as repomd_f:
            repomd_hash = hashlib.sha256(repomd_f.read()).hexdigest()
        assert repomd_hash in (metadata_dir / "repomd.xml.metalink").read_text(
            encoding="ascii"
        )


def test_installer_init_cache(artifacts_dir):
    env = os.environ.copy()
    templates_cache = artifacts_dir / "cache/installer/templates"

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "qubes-release",
        "package",
        "fetch",
    )

    # make ISO cache
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-o",
        "cache:templates+debian-12-minimal",
        "installer",
        "init-cache",
        env=env,
    )

    rpms = list(templates_cache.glob("*.rpm"))
    assert rpms
    assert rpms[0].name.startswith("qubes-template-debian-12-minimal-4.2.0")
