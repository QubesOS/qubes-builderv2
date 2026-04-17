import datetime
import hashlib
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
from qubesbuilder.common import PROJECT_PATH
from tests.conftest import artifacts_dir_single, artifacts_dir

DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder-ci.yml"
HASH_RE = re.compile(r"[a-f0-9]{40}")

# Version constants: update these when bumping component/template versions
EXAMPLE_VERSION = "1.0.2"
EXAMPLE_RELEASE = "1"
FEDORA_MINIMAL = "fedora-43-minimal"
FEDORA_XFCE = "fedora-43-xfce"
HOST_FC_DIST = "fc37"

# python-qasync version constants
QASYNC_VERSION = "0.23.0"
QASYNC_RELEASE = "2"
QASYNC_DEB_VER = f"{QASYNC_VERSION}-{QASYNC_RELEASE}+deb12u1"
QASYNC_ORIG = f"python-qasync_{QASYNC_VERSION}.orig.tar.gz"
QASYNC_DSC = f"python-qasync_{QASYNC_DEB_VER}.dsc"
QASYNC_DEBIAN = f"python-qasync_{QASYNC_DEB_VER}.debian.tar.xz"


def example_rpm(pkg, release=1, devel=None, arch="x86_64"):
    """
    Return an RPM filename for an example-advanced sub-package.
    """
    rel = (
        f"{release}.{devel}.{HOST_FC_DIST}"
        if devel is not None
        else f"{release}.{HOST_FC_DIST}"
    )
    return f"{pkg}-{EXAMPLE_VERSION}-{rel}.{arch}.rpm"


def example_srpm(pkg, release=1, devel=None):
    """
    Return an SRPM filename for an example-advanced sub-package.
    """
    rel = (
        f"{release}.{devel}.{HOST_FC_DIST}"
        if devel is not None
        else f"{release}.{HOST_FC_DIST}"
    )
    return f"{pkg}-{EXAMPLE_VERSION}-{rel}.src.rpm"


def example_deb(pkg, deb_dist="deb12u1", devel=None, arch="amd64", ext="deb"):
    """
    Return a .deb / source filename for an example-advanced package.
    """
    devel_suffix = f"+devel{devel}" if devel is not None else ""
    ver_str = f"{EXAMPLE_VERSION}-1+{deb_dist}{devel_suffix}"
    if ext in ("deb", "buildinfo", "changes"):
        return f"{pkg}_{ver_str}_{arch}.{ext}"
    if ext in ("dsc", "debian.tar.xz"):
        return f"{pkg}_{ver_str}.{ext}"
    return f"{pkg}_{EXAMPLE_VERSION}.orig.tar.gz"


def example_component_dir(artifacts_dir, dist, stage=None, release=1):
    """
    Return the path to an example-advanced component artifact directory.
    """
    base = (
        artifacts_dir
        / f"components/example-advanced/{EXAMPLE_VERSION}-{release}/{dist}"
    )
    return base / stage if stage else base


def example_dom0_prep_rpms(devel=None):
    """
    Set of RPMs predicted at prep time for example-dom0 (noarch).
    query-builtrpms includes debuginfo/debugsource even for noarch packages.
    """
    return {
        example_rpm("qubes-example-dom0", devel=devel, arch="noarch"),
        example_rpm("qubes-example-dom0-debuginfo", devel=devel, arch="noarch"),
        example_rpm(
            "qubes-example-dom0-debugsource", devel=devel, arch="noarch"
        ),
    }


def example_dom0_build_rpms(devel=None):
    """
    Set of RPMs actually produced by mock for example-dom0 (noarch).
    mock does not generate debuginfo/debugsource for noarch packages.
    """
    return {
        example_rpm("qubes-example-dom0", devel=devel, arch="noarch"),
    }


def example_dom0_srpm(devel=None):
    return example_srpm("qubes-example-dom0", devel=devel)


def example_vm_build_rpms(devel=None):
    """
    Set of RPMs produced by the example-vm build stage (noarch).
    """
    return {
        example_rpm("qubes-example-vm", devel=devel, arch="noarch"),
    }


def example_vm_srpm(devel=None):
    return example_srpm("qubes-example-vm", devel=devel)


def example_libs_build_rpms(devel=None):
    """
    Set of RPMs produced by the example-libs build stage (x86_64, with debuginfo).
    """
    return {
        example_rpm("qubes-example-libs", devel=devel),
        example_rpm("qubes-example-libs-debuginfo", devel=devel),
        example_rpm("qubes-example-libs-debugsource", devel=devel),
        example_rpm("qubes-example-libs-devel", devel=devel),
    }


def example_libs_prep_rpms(devel=None):
    return example_libs_build_rpms(devel=devel)


def example_libs_srpm(devel=None):
    return example_srpm("qubes-example-libs", devel=devel)


def example_data_prep_rpms(devel=None):
    """
    Set of RPMs predicted at prep time for example-data (noarch, with extra subpackage).
    query-builtrpms includes debuginfo/debugsource even for noarch packages.
    """
    return {
        example_rpm("qubes-example-data", devel=devel, arch="noarch"),
        example_rpm("qubes-example-data-extra", devel=devel, arch="noarch"),
        example_rpm("qubes-example-data-debuginfo", devel=devel, arch="noarch"),
        example_rpm(
            "qubes-example-data-debugsource", devel=devel, arch="noarch"
        ),
        example_rpm(
            "qubes-example-data-extra-debuginfo", devel=devel, arch="noarch"
        ),
        example_rpm(
            "qubes-example-data-extra-debugsource", devel=devel, arch="noarch"
        ),
    }


def example_data_build_rpms(devel=None):
    """
    Set of RPMs actually produced by mock for example-data (noarch, with extra subpackage).
    mock does not generate debuginfo/debugsource for noarch packages.
    """
    return {
        example_rpm("qubes-example-data", devel=devel, arch="noarch"),
        example_rpm("qubes-example-data-extra", devel=devel, arch="noarch"),
    }


def example_data_srpm(devel=None):
    return example_srpm("qubes-example-data", devel=devel)


def example_host_all_signed_rpms(devel=None):
    """
    List of all host RPMs/SRPMs that go through the sign stage.
    """
    return [
        example_dom0_srpm(devel=devel),
        *sorted(example_dom0_build_rpms(devel=devel)),
        example_data_srpm(devel=devel),
        *sorted(example_data_build_rpms(devel=devel)),
    ]


def example_repo_dir(artifacts_dir, dist):
    """
    Local repository directory for a built component.
    """
    return (
        artifacts_dir / f"repository/{dist}/example-advanced_{EXAMPLE_VERSION}"
    )


def qb_call(builder_conf, artifacts_dir, *args, **kwargs):
    cmd = [
        "python3",
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={str(artifacts_dir)}",
        *args,
    ]
    check = kwargs.pop("check", True)
    try:
        result = subprocess.run(cmd, check=check, **kwargs)
        return result.returncode
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"Command failed:\n{' '.join(e.cmd)}\n"
            f"Return code: {e.returncode}"
        )


def qb_call_output(builder_conf, artifacts_dir, *args, **kwargs):
    cmd = [
        "python3",
        str(PROJECT_PATH / "qb"),
        "--verbose",
        "--builder-conf",
        str(builder_conf),
        "--option",
        f"artifacts-dir={str(artifacts_dir)}",
        *args,
    ]
    try:
        return subprocess.check_output(cmd, stderr=subprocess.STDOUT, **kwargs)
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"Command failed:\n{' '.join(e.cmd)}\n"
            f"Return code: {e.returncode}\n"
            f"Output:\n{e.output.decode() if e.output else ''}"
        )


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
            f.write("- vm-fc43\n")
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
            f.write("- vm-fc43\n")
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
    for component in ["example-advanced"]:
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
        artifacts_dir
        / "distfiles/example-advanced/9FA64B92F95E706BF28E2CA6484010B5CDC576E2"
    ).exists()

    assert (
        artifacts_dir / "sources" / "example-advanced" / ".qubesbuilder"
    ).exists()


def test_common_component_fetch_updating(artifacts_dir):
    infos_before = _get_infos(artifacts_dir)

    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "skip-git-fetch=false",
        "package",
        "fetch",
    ).decode()
    for sentence in [
        "example-advanced: source already fetched. Updating.",
        "example-advanced: file 9FA64B92F95E706BF28E2CA6484010B5CDC576E2 already downloaded. Skipping.",
    ]:
        assert sentence in result

    infos_after = _get_infos(artifacts_dir)

    for component in infos_before:
        assert (
            infos_after[component]["btime"] != infos_before[component]["btime"]
        )


def test_common_component_fetch_inplace_updating(artifacts_dir):
    infos_before = _get_infos(artifacts_dir)

    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "skip-git-fetch=false",
        "--option",
        "git-run-inplace=true",
        "package",
        "fetch",
    ).decode()

    for sentence in [
        "example-advanced: source already fetched. Updating.",
        "example-advanced: file 9FA64B92F95E706BF28E2CA6484010B5CDC576E2 already downloaded. Skipping.",
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

    _get_infos(artifacts_dir)

    assert (
        artifacts_dir / "sources" / "example-advanced" / ".qubesbuilder"
    ).exists()
    # With skip-files-fetch, external source files must not be downloaded;
    # submodule archives are part of the source fetch and are allowed.
    distfiles = list(
        (artifacts_dir / "distfiles" / "example-advanced").iterdir()
    )
    assert not any(
        f.name == "9FA64B92F95E706BF28E2CA6484010B5CDC576E2" for f in distfiles
    )


def test_common_component_fetch_commit_fresh(artifacts_dir_single):
    artifacts_dir = artifacts_dir_single
    # Fetch a known tag and verify the exact commit hash is recorded.
    commit_sha = "03e6ccfa71cf824fc063c97dbf22b52244011958"
    qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-o",
        f"+components+example-advanced:branch=v1.0.2-1",
        "package",
        "fetch",
    ).decode()

    fetch_artifact = (
        artifacts_dir
        / f"components/example-advanced/{EXAMPLE_VERSION}-{EXAMPLE_RELEASE}/nodist/fetch/source.fetch.yml"
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
    result = qb_call(
        DEFAULT_BUILDER_CONF, artifacts_dir, "non-existent-command", check=False
    )
    assert result == 2


def test_common_non_existent_component(artifacts_dir):
    result = qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "non-existent-component",
        "package",
        "all",
        check=False,
    )
    assert result == 1


def test_common_component_dependencies_01(artifacts_dir_single):
    # Test that example-advanced Windows build stage artifacts can be pre-staged
    # and that the pipeline correctly resolves vm-win10 dist-specific build outputs.
    # This valides the pipeline artifact "needs" path for Windows components.
    artifacts_dir = artifacts_dir_single
    win_dist_dir = example_component_dir(artifacts_dir, "vm-win10", stage=None)

    # Pre-stage fake Windows build artifacts as the Windows executor would produce them.
    win_build_dir = win_dist_dir / "build"
    win_build_dir.mkdir(parents=True, exist_ok=True)

    dll_rel = "windows/vs2022/x64/Release/example-dummy/example-dummy.dll"
    lib_rel = "windows/vs2022/x64/Release/example-dummy/example-dummy.lib"

    win_build_yml = win_build_dir / "example-advanced.sln.build.yml"
    win_build_yml.write_text(yaml.dump({"files": [dll_rel, lib_rel]}))

    dll_path = win_build_dir / dll_rel
    dll_path.parent.mkdir(parents=True, exist_ok=True)
    dll_path.write_bytes(b"\x4d\x5a\x00\x00")  # minimal MZ header stub

    lib_path = win_build_dir / lib_rel
    lib_path.parent.mkdir(parents=True, exist_ok=True)
    lib_path.write_bytes(b"\x21\x3c\x61\x72")  # minimal AR stub

    # Verify the fake artifacts are present at the expected pipeline locations.
    assert win_build_yml.exists()
    assert dll_path.exists()
    assert lib_path.exists()


#
# Pipeline for example-advanced and host-fc37
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
    assert (
        artifacts_dir
        / "cache/chroot/host-fc37/fedora-37-x86_64/root_cache/cache.tar.gz"
    ).exists()


def test_component_host_fc37_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "qubes-release",
        "-c",
        "example-advanced",
        "-d",
        "host-fc37",
        "package",
        "fetch",
        "prep",
    )

    prep_dir = example_component_dir(artifacts_dir, "host-fc37", "prep")

    with open(prep_dir / "rpm_spec_example-dom0.spec.prep.yml") as f:
        info = yaml.safe_load(f.read())

    assert set(info.get("rpms", [])) == example_dom0_prep_rpms()
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == example_dom0_srpm()

    with open(prep_dir / "rpm_spec_example-data.spec.prep.yml") as f:
        info = yaml.safe_load(f.read())

    assert set(info.get("rpms", [])) == example_data_prep_rpms()
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == example_data_srpm()


def test_component_host_fc37_build(artifacts_dir):
    # Clean stale build artifacts and local repository so the build is not
    # skipped and the repository/ dir is repopulated with the correct release.
    build_dir_path = example_component_dir(artifacts_dir, "host-fc37", "build")
    if build_dir_path.exists():
        shutil.rmtree(build_dir_path)
    repo_dir_path = artifacts_dir / "repository" / "host-fc37"
    if repo_dir_path.exists():
        shutil.rmtree(repo_dir_path)

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "host-fc37",
        "package",
        "build",
    )

    build_dir = example_component_dir(artifacts_dir, "host-fc37", "build")
    repo_dir = example_repo_dir(artifacts_dir, "host-fc37")

    with open(build_dir / "rpm_spec_example-dom0.spec.build.yml") as f:
        info = yaml.safe_load(f.read())

    rpms = example_dom0_build_rpms()
    srpm = example_dom0_srpm()
    for pkg in rpms | {srpm}:
        assert (repo_dir / pkg).exists()
    assert set(info.get("rpms", [])) == rpms
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == srpm

    with open(build_dir / "rpm_spec_example-data.spec.build.yml") as f:
        info = yaml.safe_load(f.read())

    rpms = example_data_build_rpms()
    srpm = example_data_srpm()
    for pkg in rpms | {srpm}:
        assert (repo_dir / pkg).exists()
    assert set(info.get("rpms", [])) == rpms
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("srpm", None) == srpm


def test_component_host_fc37_sign(artifacts_dir):
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
            "example-advanced",
            "-d",
            "host-fc37",
            "package",
            "sign",
            env=env,
        )

    dbpath = artifacts_dir / "rpmdb/632F8C69E01B25C9E0C3ADF2F360C0D259FB650C"
    assert dbpath.exists()

    comp_dir = example_component_dir(artifacts_dir, "host-fc37")
    for rpm in example_host_all_signed_rpms():
        rpm_path = (
            comp_dir
            / ("prep" if rpm.endswith(".src.rpm") else "build/rpm")
            / rpm
        )
        assert rpm_path.exists()
        result = subprocess.run(
            f"rpm --dbpath {dbpath} -K {rpm_path}",
            check=True,
            capture_output=True,
            shell=True,
        )
        assert "digests signatures OK" in result.stdout.decode()


def test_component_host_fc37_publish(artifacts_dir):
    # Clean stale publish state from previous runs to ensure a fresh start.
    publish_dir = example_component_dir(artifacts_dir, "host-fc37", "publish")
    if publish_dir.exists():
        shutil.rmtree(publish_dir)
    repo_publish_dir = artifacts_dir / "repository-publish"
    if repo_publish_dir.exists():
        shutil.rmtree(repo_publish_dir)

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
            "example-advanced",
            "-d",
            "host-fc37",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        rpms_dom0 = example_dom0_build_rpms()
        srpm_dom0 = example_dom0_srpm()
        rpms_data = example_data_build_rpms()
        srpm_data = example_data_srpm()

        publish_dir = example_component_dir(
            artifacts_dir, "host-fc37", "publish"
        )
        with open(publish_dir / "rpm_spec_example-dom0.spec.publish.yml") as f:
            info_dom0 = yaml.safe_load(f.read())
        with open(publish_dir / "rpm_spec_example-data.spec.publish.yml") as f:
            info_data = yaml.safe_load(f.read())

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert ["unstable"] == [
            r["name"] for r in info_dom0.get("repository-publish", [])
        ]

        assert set(info_data.get("rpms", [])) == rpms_data
        assert info_data.get("srpm", None) == srpm_data
        assert HASH_RE.match(info_data.get("source-hash", None))
        assert ["unstable"] == [
            r["name"] for r in info_data.get("repository-publish", [])
        ]

        # publish into current-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "host-fc37",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )
        with open(publish_dir / "rpm_spec_example-dom0.spec.publish.yml") as f:
            info_dom0 = yaml.safe_load(f.read())
        with open(publish_dir / "rpm_spec_example-data.spec.publish.yml") as f:
            info_data = yaml.safe_load(f.read())

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert set(
            [r["name"] for r in info_dom0.get("repository-publish", [])]
        ) == {"unstable", "current-testing"}

        assert set(info_data.get("rpms", [])) == rpms_data
        assert info_data.get("srpm", None) == srpm_data
        assert HASH_RE.match(info_data.get("source-hash", None))
        assert set(
            [r["name"] for r in info_data.get("repository-publish", [])]
        ) == {"unstable", "current-testing"}

        assert (
            artifacts_dir
            / "repository-publish/rpm/r4.2/current-testing/host/fc37"
        ).exists()

        # publish into current
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")
        publish_dom0_file = (
            publish_dir / "rpm_spec_example-dom0.spec.publish.yml"
        )
        publish_data_file = (
            publish_dir / "rpm_spec_example-data.spec.publish.yml"
        )

        for r in info_dom0["repository-publish"]:
            if r["name"] == "current-testing":
                r["timestamp"] = fake_time
                break
        for r in info_data["repository-publish"]:
            if r["name"] == "current-testing":
                r["timestamp"] = fake_time
                break

        with open(publish_dom0_file, "w") as f:
            f.write(yaml.safe_dump(info_dom0))
        with open(publish_data_file, "w") as f:
            f.write(yaml.safe_dump(info_data))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "host-fc37",
            "repository",
            "publish",
            "current",
            env=env,
        )
        with open(publish_dom0_file) as f:
            info_dom0 = yaml.safe_load(f.read())
        with open(publish_data_file) as f:
            info_data = yaml.safe_load(f.read())

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert set(
            [r["name"] for r in info_dom0.get("repository-publish", [])]
        ) == {"unstable", "current-testing", "current"}

        assert set(info_data.get("rpms", [])) == rpms_data
        assert info_data.get("srpm", None) == srpm_data
        assert HASH_RE.match(info_data.get("source-hash", None))
        assert set(
            [r["name"] for r in info_data.get("repository-publish", [])]
        ) == {"unstable", "current-testing", "current"}

        metadata_dir = (
            artifacts_dir
            / "repository-publish/rpm/r4.2/current/host/fc37/repodata"
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

        assert (
            artifacts_dir / "repository-publish/rpm/r4.2/current/host/fc37"
        ).exists()

    all_rpms = (
        example_dom0_build_rpms()
        | {example_dom0_srpm()}
        | example_data_build_rpms()
        | {example_data_srpm()}
    )

    # Check that packages are in the published repository
    for repository in ["unstable", "current-testing", "current"]:
        repository_dir = f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}/host/fc37"
        packages = rpm_packages_list(repository_dir)
        assert all_rpms == set(packages)


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
            "example-advanced",
            "-d",
            "host-fc37",
            "repository",
            "upload",
            "unstable",
            env=env,
        )

        all_rpms = (
            example_dom0_build_rpms()
            | {example_dom0_srpm()}
            | example_data_build_rpms()
            | {example_data_srpm()}
        )
        for rpm in all_rpms:
            assert (
                pathlib.Path(tmpdir)
                / f"repo/rpm/r4.2/unstable/host/fc37/rpm/{rpm}"
            ).exists()

        # vm-fc43 shouldn't exist, as nothing was published into it
        assert not (
            pathlib.Path(tmpdir) / f"repo/rpm/r4.2/unstable/vm/fc43"
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
        "example-advanced",
        "-d",
        "host-fc37",
        "package",
        "prep",
    ).decode()
    print(result)
    assert (
        "example-advanced:host-fedora-37.x86_64: Source hash is the same than already prepared source. Skipping."
        in result
    )


def test_component_host_fc37_build_skip(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "host-fc37",
        "package",
        "build",
    ).decode()
    print(result)
    assert (
        "example-advanced:host-fedora-37.x86_64: Source hash is the same than already built source. Skipping."
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
            "example-advanced",
            "-d",
            "host-fc37",
            "package",
            "sign",
            env=env,
        ).decode()

    for rpm in example_host_all_signed_rpms():
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

        result = qb_call_output(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "host-fc37",
            "-o",
            "automatic-upload-on-publish=true",
            "repository",
            "unpublish",
            "current",
            env=env,
        ).decode()

        rpms_dom0 = example_dom0_build_rpms()
        srpm_dom0 = example_dom0_srpm()
        rpms_data = example_data_build_rpms()
        srpm_data = example_data_srpm()

        publish_dir = example_component_dir(
            artifacts_dir, "host-fc37", "publish"
        )
        with open(publish_dir / "rpm_spec_example-dom0.spec.publish.yml") as f:
            info_dom0 = yaml.safe_load(f.read())
        with open(publish_dir / "rpm_spec_example-data.spec.publish.yml") as f:
            info_data = yaml.safe_load(f.read())

        assert set(info_dom0.get("rpms", [])) == rpms_dom0
        assert info_dom0.get("srpm", None) == srpm_dom0
        assert HASH_RE.match(info_dom0.get("source-hash", None))
        assert set(
            [r["name"] for r in info_dom0.get("repository-publish", [])]
        ) == {"unstable", "current-testing"}

        assert set(info_data.get("rpms", [])) == rpms_data
        assert info_data.get("srpm", None) == srpm_data
        assert HASH_RE.match(info_data.get("source-hash", None))
        assert set(
            [r["name"] for r in info_data.get("repository-publish", [])]
        ) == {"unstable", "current-testing"}

    # Check that packages are in the published repository
    all_rpms = (
        example_dom0_build_rpms()
        | {example_dom0_srpm()}
        | example_data_build_rpms()
        | {example_data_srpm()}
    )
    for repository in ["unstable", "current-testing", "current"]:
        repository_dir = f"file://{artifacts_dir}/repository-publish/rpm/r4.2/{repository}/host/fc37"
        packages = rpm_packages_list(repository_dir)
        if repository == "current":
            assert packages == []
        else:
            assert all_rpms == set(packages)

    assert "[qb.publish_rpm.example-advanced.host-fc37]" in result
    assert "[qb.upload.host-fc37]" in result


#
# Pipeline for example-advanced and vm-bookworm
#

# DEB version strings for example-advanced on bookworm
EXAMPLE_DEB_VER = f"{EXAMPLE_VERSION}-1+deb12u1"
EXAMPLE_DEB_ORIG = f"qubes-example-advanced_{EXAMPLE_VERSION}.orig.tar.gz"
EXAMPLE_DEB_DEBIAN = f"qubes-example-advanced_{EXAMPLE_DEB_VER}.debian.tar.xz"
EXAMPLE_DEB_DSC = f"qubes-example-advanced_{EXAMPLE_DEB_VER}.dsc"
EXAMPLE_PKG_RELEASE_NAME = f"qubes-example-advanced_{EXAMPLE_VERSION}"
EXAMPLE_PKG_RELEASE_NAME_FULL = f"qubes-example-advanced_{EXAMPLE_DEB_VER}"


def test_component_vm_bookworm_init_cache(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-bookworm",
        "package",
        "init-cache",
    )
    assert (
        artifacts_dir
        / "cache/chroot/vm-bookworm/debian-12-amd64/pbuilder/base.tgz"
    ).exists()


def test_component_vm_bookworm_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "vm-bookworm",
        "package",
        "fetch",
        "prep",
    )

    prep_dir = example_component_dir(artifacts_dir, "vm-bookworm", "prep")
    with open(prep_dir / "debian.prep.yml") as f:
        info = yaml.safe_load(f.read())

    # prep lists all packages including dbgsym variants
    packages = [
        f"qubes-example-advanced_{EXAMPLE_DEB_VER}_all.deb",
        f"qubes-example-advanced-dbgsym_{EXAMPLE_DEB_VER}_all.deb",
        f"qubes-example-advanced-dbgsym_{EXAMPLE_DEB_VER}_all.ddeb",
        f"qubes-example-advanced-libs_{EXAMPLE_DEB_VER}_amd64.deb",
        f"qubes-example-advanced-libs-dbgsym_{EXAMPLE_DEB_VER}_amd64.deb",
        f"qubes-example-advanced-libs-dbgsym_{EXAMPLE_DEB_VER}_amd64.ddeb",
        f"qubes-example-advanced-dev_{EXAMPLE_DEB_VER}_amd64.deb",
        f"qubes-example-advanced-dev-dbgsym_{EXAMPLE_DEB_VER}_amd64.deb",
        f"qubes-example-advanced-dev-dbgsym_{EXAMPLE_DEB_VER}_amd64.ddeb",
    ]
    assert set(info.get("packages", [])) == set(packages)
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("debian", None) == EXAMPLE_DEB_DEBIAN
    assert info.get("dsc", None) == EXAMPLE_DEB_DSC
    assert info.get("orig", None) == EXAMPLE_DEB_ORIG
    assert info.get("package-release-name", None) == EXAMPLE_PKG_RELEASE_NAME
    assert (
        info.get("package-release-name-full", None)
        == EXAMPLE_PKG_RELEASE_NAME_FULL
    )


def test_component_vm_bookworm_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "vm-bookworm",
        "package",
        "build",
    )

    build_dir = example_component_dir(artifacts_dir, "vm-bookworm", "build")
    with open(build_dir / "debian.build.yml") as f:
        info = yaml.safe_load(f.read())

    packages = [
        f"qubes-example-advanced_{EXAMPLE_DEB_VER}_all.deb",
        f"qubes-example-advanced-libs_{EXAMPLE_DEB_VER}_amd64.deb",
        f"qubes-example-advanced-libs-dbgsym_{EXAMPLE_DEB_VER}_amd64.deb",
        f"qubes-example-advanced-dev_{EXAMPLE_DEB_VER}_amd64.deb",
    ]
    assert set(info.get("packages", [])) == set(packages)
    assert HASH_RE.match(info.get("source-hash", None))
    assert info.get("debian", None) == EXAMPLE_DEB_DEBIAN
    assert info.get("dsc", None) == EXAMPLE_DEB_DSC
    assert info.get("orig", None) == EXAMPLE_DEB_ORIG
    assert info.get("package-release-name", None) == EXAMPLE_PKG_RELEASE_NAME
    assert (
        info.get("package-release-name-full", None)
        == EXAMPLE_PKG_RELEASE_NAME_FULL
    )

    files = [
        EXAMPLE_DEB_DSC,
        f"qubes-example-advanced_{EXAMPLE_DEB_VER}_amd64.changes",
        f"qubes-example-advanced_{EXAMPLE_DEB_VER}_amd64.buildinfo",
    ]
    for file in files:
        file_path = build_dir / file
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
            "example-advanced",
            "-d",
            "vm-bookworm",
            "package",
            "sign",
            env=env,
        )

    keyring_dir = example_component_dir(
        artifacts_dir, "vm-bookworm", "sign/keyring"
    )
    assert keyring_dir.exists()

    files = [
        EXAMPLE_DEB_DSC,
        f"qubes-example-advanced_{EXAMPLE_DEB_VER}_amd64.changes",
        f"qubes-example-advanced_{EXAMPLE_DEB_VER}_amd64.buildinfo",
    ]
    build_dir = example_component_dir(artifacts_dir, "vm-bookworm", "build")
    for f in files:
        file_path = build_dir / f
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
    # Clean stale publish state from previous runs to ensure a fresh start.
    stale_publish_dir = example_component_dir(
        artifacts_dir, "vm-bookworm", "publish"
    )
    if stale_publish_dir.exists():
        shutil.rmtree(stale_publish_dir)
    stale_deb_repo = artifacts_dir / "repository-publish" / "deb"
    if stale_deb_repo.exists():
        shutil.rmtree(stale_deb_repo)

    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        publish_dir = example_component_dir(
            artifacts_dir, "vm-bookworm", "publish"
        )
        publish_file = publish_dir / "debian.publish.yml"

        # publish into unstable
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "vm-bookworm",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        build_packages = [
            f"qubes-example-advanced_{EXAMPLE_DEB_VER}_all.deb",
            f"qubes-example-advanced-libs_{EXAMPLE_DEB_VER}_amd64.deb",
            f"qubes-example-advanced-libs-dbgsym_{EXAMPLE_DEB_VER}_amd64.deb",
            f"qubes-example-advanced-dev_{EXAMPLE_DEB_VER}_amd64.deb",
        ]
        assert set(info.get("packages", [])) == set(build_packages)
        assert HASH_RE.match(info.get("source-hash", None))
        assert info.get("debian", None) == EXAMPLE_DEB_DEBIAN
        assert info.get("dsc", None) == EXAMPLE_DEB_DSC
        assert info.get("orig", None) == EXAMPLE_DEB_ORIG
        assert (
            info.get("package-release-name", None) == EXAMPLE_PKG_RELEASE_NAME
        )
        assert (
            info.get("package-release-name-full", None)
            == EXAMPLE_PKG_RELEASE_NAME_FULL
        )
        assert ["unstable"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into current-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "vm-bookworm",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert set(info.get("packages", [])) == set(build_packages)
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        # publish into current
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")

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
            "example-advanced",
            "-d",
            "vm-bookworm",
            "repository",
            "publish",
            "current",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert set(info.get("packages", [])) == set(build_packages)
        assert HASH_RE.match(info.get("source-hash", None))
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
            f"{codename}|main|amd64: qubes-example-advanced {EXAMPLE_DEB_VER}",
            f"{codename}|main|amd64: qubes-example-advanced-libs {EXAMPLE_DEB_VER}",
            f"{codename}|main|amd64: qubes-example-advanced-libs-dbgsym {EXAMPLE_DEB_VER}",
            f"{codename}|main|amd64: qubes-example-advanced-dev {EXAMPLE_DEB_VER}",
            f"{codename}|main|source: qubes-example-advanced {EXAMPLE_DEB_VER}",
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
        "example-advanced",
        "-d",
        "vm-bookworm",
        "package",
        "prep",
    ).decode()
    print(result)
    assert (
        "example-advanced:vm-debian-12.amd64: Source hash is the same than already prepared source. Skipping."
        in result
    )


def test_component_vm_bookworm_build_skip(artifacts_dir):
    result = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "vm-bookworm",
        "package",
        "build",
    ).decode()
    print(result)
    assert (
        "example-advanced:vm-debian-12.amd64: Source hash is the same than already built source. Skipping."
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
            "example-advanced",
            "-d",
            "vm-bookworm",
            "package",
            "sign",
            env=env,
        ).decode()

    assert "Leaving current signature unchanged." in result


# Check that we unpublish properly from current-testing


def test_component_vm_bookworm_unpublish(artifacts_dir):
    # FIXME: we rely on previous test_publish_vm_bookworm being ran before
    repository_dir = artifacts_dir / "repository-publish/deb/r4.2/vm"
    for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
        packages = deb_packages_list(repository_dir, codename)
        expected_packages = [
            f"{codename}|main|amd64: qubes-example-advanced {EXAMPLE_DEB_VER}",
            f"{codename}|main|amd64: qubes-example-advanced-libs {EXAMPLE_DEB_VER}",
            f"{codename}|main|amd64: qubes-example-advanced-libs-dbgsym {EXAMPLE_DEB_VER}",
            f"{codename}|main|amd64: qubes-example-advanced-dev {EXAMPLE_DEB_VER}",
            f"{codename}|main|source: qubes-example-advanced {EXAMPLE_DEB_VER}",
        ]
        assert set(packages) == set(expected_packages)

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
            "example-advanced",
            "-d",
            "vm-bookworm",
            "repository",
            "unpublish",
            "current",
            env=env,
        )

        publish_dir = example_component_dir(
            artifacts_dir, "vm-bookworm", "publish"
        )
        with open(publish_dir / "debian.publish.yml") as f:
            info = yaml.safe_load(f.read())

        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

    # Check that packages are in the published repositories
    for codename in ["bookworm-unstable", "bookworm-testing", "bookworm"]:
        packages = deb_packages_list(repository_dir, codename)
        if codename == "bookworm":
            expected_packages = []
        else:
            expected_packages = [
                f"{codename}|main|amd64: qubes-example-advanced {EXAMPLE_DEB_VER}",
                f"{codename}|main|amd64: qubes-example-advanced-libs {EXAMPLE_DEB_VER}",
                f"{codename}|main|amd64: qubes-example-advanced-libs-dbgsym {EXAMPLE_DEB_VER}",
                f"{codename}|main|amd64: qubes-example-advanced-dev {EXAMPLE_DEB_VER}",
                f"{codename}|main|source: qubes-example-advanced {EXAMPLE_DEB_VER}",
            ]
        assert set(packages) == set(expected_packages)


def test_increment_component_fetch(artifacts_dir):
    # Reset devel counters so the test is idempotent across runs.
    devel_file = (
        artifacts_dir
        / "components"
        / "example-advanced"
        / "noversion"
        / "devel"
    )
    if devel_file.exists():
        devel_file.unlink()

    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "--option",
        "increment-devel-versions=true",
        "package",
        "fetch",
    )

    assert (
        artifacts_dir
        / "components"
        / "example-advanced"
        / "noversion"
        / "devel"
    ).exists()

    (artifacts_dir / "sources" / "example-advanced" / "hello").write_text(
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

    assert (
        artifacts_dir
        / "components"
        / "example-advanced"
        / "noversion"
        / "devel"
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
            / "components/example-advanced/noversion/devel"
        )
        devel_path.parent.mkdir(parents=True, exist_ok=True)
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
            artifacts_dir / "components/example-advanced/noversion/devel"
        ).read_text(encoding="utf-8") == "42"

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "example-advanced",
            "-d",
            "host-fc37",
            "-d",
            "vm-bookworm",
            "package",
            "all",
            env=env,
        )

    repo_dir = example_repo_dir(artifacts_dir, "host-fc37")
    for pkg in example_dom0_build_rpms(devel=42) | {
        example_dom0_srpm(devel=42)
    }:
        assert (repo_dir / pkg).exists()
    for pkg in example_data_build_rpms(devel=42) | {
        example_data_srpm(devel=42)
    }:
        assert (repo_dir / pkg).exists()

    deb_ver = f"{EXAMPLE_VERSION}-1+deb12u1+devel42"
    deb_files = [
        f"qubes-example-advanced_{deb_ver}_all.deb",
        f"qubes-example-advanced-libs_{deb_ver}_amd64.deb",
        f"qubes-example-advanced-dev_{deb_ver}_amd64.deb",
        f"qubes-example-advanced_{deb_ver}.debian.tar.xz",
        f"qubes-example-advanced_{deb_ver}.dsc",
        f"qubes-example-advanced_{deb_ver}_amd64.buildinfo",
        f"qubes-example-advanced_{deb_ver}_amd64.changes",
        f"qubes-example-advanced_{EXAMPLE_VERSION}.orig.tar.gz",
    ]
    repo_deb_dir = example_repo_dir(artifacts_dir, "vm-bookworm")
    for file in deb_files:
        assert (repo_deb_dir / file).exists()


def test_publish_deb_no_source_conflict(artifacts_dir_single):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)
        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        # First build cycle (devel=1): fetch, prep, build, sign
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "example-advanced",
            "-d",
            "vm-trixie",
            "package",
            "fetch",
            "prep",
            "build",
            "sign",
            env=env,
        )

        assert (
            artifacts_dir_single / "components/example-advanced/noversion/devel"
        ).read_text(encoding="utf-8") == "1"

        # Publish devel=1 to current-testing: registers orig.tar.gz (checksum A)
        # in the reprepro pool alongside the binary and source packages.
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "example-advanced",
            "-d",
            "vm-trixie",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )

        repository_dir = artifacts_dir_single / "repository-publish/deb/r4.2/vm"
        packages = deb_packages_list(repository_dir, "trixie-testing")
        assert any(
            "qubes-example-advanced" in p and "devel1" in p for p in packages
        )
        assert any(
            "|main|source: qubes-example-advanced" in p for p in packages
        )

        # Modify the upstream source so the rebuilt orig.tar.gz has a different
        # checksum than the one already in the reprepro pool.
        (artifacts_dir_single / "sources/example-advanced/hello").write_text(
            "world", encoding="utf8"
        )

        # Second fetch increments devel from 1 to 2.
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "example-advanced",
            "-d",
            "vm-trixie",
            "package",
            "fetch",
            env=env,
        )

        assert (
            artifacts_dir_single / "components/example-advanced/noversion/devel"
        ).read_text(encoding="utf-8") == "2"

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "example-advanced",
            "-d",
            "vm-trixie",
            "package",
            "prep",
            "build",
            "sign",
            env=env,
        )

        # Publish devel=2 to unstable.  The publish detects the source-hash
        # change in history, unpublishes devel=1 from current-testing (with
        # --delete so the stale orig.tar.gz is removed from the pool), then
        # publishes the full .changes for devel=2 including the updated orig.tar.gz.
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir_single,
            "--option",
            "increment-devel-versions=true",
            "-c",
            "example-advanced",
            "-d",
            "vm-trixie",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        packages = deb_packages_list(repository_dir, "trixie-unstable")
        assert any(
            "qubes-example-advanced" in p and "devel2" in p for p in packages
        )
        assert any(
            "|main|source: qubes-example-advanced" in p for p in packages
        )

        # The stale devel=1 publication must have been cleaned from current-testing
        # (its orig.tar.gz removed from the pool) before devel=2 could be published.
        packages_testing = deb_packages_list(repository_dir, "trixie-testing")
        assert not any(
            "qubes-example-advanced" in p and "devel1" in p
            for p in packages_testing
        )
        assert not any(
            "|main|source: qubes-example-advanced" in p and "devel1" in p
            for p in packages_testing
        )


#
# Pipeline for example-advanced and vm-archlinux
#

EXAMPLE_ARCH_PKG = f"qubes-example-advanced-{EXAMPLE_VERSION}-{EXAMPLE_RELEASE}-x86_64.pkg.tar.zst"
EXAMPLE_ARCH_ARCHIVE = (
    f"qubes-example-advanced-{EXAMPLE_VERSION}-{EXAMPLE_RELEASE}.tar.gz"
)


def test_component_vm_archlinux_init_cache(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-archlinux",
        "package",
        "init-cache",
    )
    assert (
        artifacts_dir
        / "cache/chroot/vm-archlinux/archlinux-rolling-x86_64/root.tar.gz"
    ).exists()


def test_component_vm_archlinux_prep(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "vm-archlinux",
        "package",
        "fetch",
        "prep",
    )

    prep_dir = example_component_dir(artifacts_dir, "vm-archlinux", "prep")
    with open(prep_dir / "archlinux.prep.yml") as f:
        info = yaml.safe_load(f.read())

    assert EXAMPLE_ARCH_PKG in info.get("packages", [])
    assert info.get("source-archive", None) == EXAMPLE_ARCH_ARCHIVE
    assert HASH_RE.match(info.get("source-hash", None))


def test_component_vm_archlinux_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        "example-advanced",
        "-d",
        "vm-archlinux",
        "package",
        "build",
    )

    build_dir = example_component_dir(artifacts_dir, "vm-archlinux", "build")
    with open(build_dir / "archlinux.build.yml") as f:
        info = yaml.safe_load(f.read())

    assert EXAMPLE_ARCH_PKG in info.get("packages", [])
    assert info.get("source-archive", None) == EXAMPLE_ARCH_ARCHIVE
    assert HASH_RE.match(info.get("source-hash", None))

    pkg_path = build_dir / "pkgs" / EXAMPLE_ARCH_PKG
    assert pkg_path.exists()


def test_component_vm_archlinux_sign(artifacts_dir):
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
            "example-advanced",
            "-d",
            "vm-archlinux",
            "package",
            "sign",
            env=env,
        )
        pkgs_dir = (
            example_component_dir(artifacts_dir, "vm-archlinux", "build")
            / "pkgs"
        )
        pkg_path = pkgs_dir / EXAMPLE_ARCH_PKG
        pkg_sig_path = pkgs_dir / (EXAMPLE_ARCH_PKG + ".sig")
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
    # Clean stale publish state from previous runs to ensure a fresh start.
    stale_publish_dir = example_component_dir(
        artifacts_dir, "vm-archlinux", "publish"
    )
    if stale_publish_dir.exists():
        shutil.rmtree(stale_publish_dir)
    stale_arch_repo = artifacts_dir / "repository-publish" / "archlinux"
    if stale_arch_repo.exists():
        shutil.rmtree(stale_arch_repo)

    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        publish_dir = example_component_dir(
            artifacts_dir, "vm-archlinux", "publish"
        )
        publish_file = publish_dir / "archlinux.publish.yml"

        # publish into unstable
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "vm-archlinux",
            "repository",
            "publish",
            "unstable",
            env=env,
        )

        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert EXAMPLE_ARCH_PKG in info.get("packages", [])
        assert HASH_RE.match(info.get("source-hash", None))
        assert ["unstable"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into current-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-c",
            "example-advanced",
            "-d",
            "vm-archlinux",
            "repository",
            "publish",
            "current-testing",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert EXAMPLE_ARCH_PKG in info.get("packages", [])
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
        }

        # publish into current
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")

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
            "example-advanced",
            "-d",
            "vm-archlinux",
            "repository",
            "publish",
            "current",
            env=env,
        )
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert EXAMPLE_ARCH_PKG in info.get("packages", [])
        assert HASH_RE.match(info.get("source-hash", None))
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "unstable",
            "current-testing",
            "current",
        }

        qubesdb = (
            artifacts_dir
            / "repository-publish/archlinux/r4.2/current/vm/archlinux/pkgs/qubes-r4.2-current.db.tar.gz"
        )
        assert qubesdb.exists()

        qubesdb_sig = (
            artifacts_dir
            / "repository-publish/archlinux/r4.2/current/vm/archlinux/pkgs/qubes-r4.2-current.db.tar.gz.sig"
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
            "example-advanced",
            "-d",
            "vm-archlinux",
            "repository",
            "upload",
            "unstable",
            env=env,
        )

        assert (
            pathlib.Path(tmpdir)
            / f"repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/{EXAMPLE_ARCH_PKG}"
        ).exists()

        assert (
            pathlib.Path(tmpdir)
            / f"repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/{EXAMPLE_ARCH_PKG}.sig"
        ).exists()

        assert (
            pathlib.Path(tmpdir)
            / "repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/qubes-r4.2-unstable.db.tar.gz"
        ).exists()

        assert (
            pathlib.Path(tmpdir)
            / "repo/archlinux/r4.2/unstable/vm/archlinux/pkgs/qubes-r4.2-unstable.db.tar.gz.sig"
        ).exists()

        # vm-fc43 shouldn't exist, as nothing was published into it
        assert not (
            pathlib.Path(tmpdir) / "repo/rpm/r4.2/unstable/vm/fc43"
        ).exists()

        # and vm-bookworm same
        assert not (
            pathlib.Path(tmpdir) / "repo/deb/r4.2/vm/dists/bookworm-unstable"
        ).exists()


#
# Pipeline for components using source:files: without create-archive.
# Validates that the external file is used as the .orig.tar.gz for deb builds.
# Add entries here to cover additional components with the same feature.
#

FILES_ONLY_COMPONENTS = [
    pytest.param(
        {
            "component": "python-qasync",
            "version": QASYNC_VERSION,
            "release": QASYNC_RELEASE,
            "prep_yml": "debian-pkg_debian.prep.yml",
            "build_yml": "debian-pkg_debian.build.yml",
            "orig": QASYNC_ORIG,
            "dsc": QASYNC_DSC,
            "debian": QASYNC_DEBIAN,
            "package": f"python3-qasync_{QASYNC_DEB_VER}_all.deb",
        },
        id="python-qasync",
    ),
]


def files_only_component_dir(
    artifacts_dir, component, version, release, dist, stage=None
):
    base = artifacts_dir / f"components/{component}/{version}-{release}/{dist}"
    return base / stage if stage else base


@pytest.mark.parametrize("cfg", FILES_ONLY_COMPONENTS)
def test_component_vm_bookworm_files_only_prep(artifacts_dir, cfg):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        cfg["component"],
        "-d",
        "vm-bookworm",
        "package",
        "fetch",
        "prep",
    )

    prep_dir = files_only_component_dir(
        artifacts_dir,
        cfg["component"],
        cfg["version"],
        cfg["release"],
        "vm-bookworm",
        "prep",
    )
    with open(prep_dir / cfg["prep_yml"]) as f:
        info = yaml.safe_load(f.read())

    # The .orig.tar.gz must be the external file, not a component archive.
    assert info.get("orig", None) == cfg["orig"]
    assert info.get("dsc", None) == cfg["dsc"]
    assert info.get("debian", None) == cfg["debian"]
    assert HASH_RE.match(info.get("source-hash", ""))
    assert cfg["package"] in info.get("packages", [])


@pytest.mark.parametrize("cfg", FILES_ONLY_COMPONENTS)
def test_component_vm_bookworm_files_only_build(artifacts_dir, cfg):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-c",
        cfg["component"],
        "-d",
        "vm-bookworm",
        "package",
        "build",
    )

    build_dir = files_only_component_dir(
        artifacts_dir,
        cfg["component"],
        cfg["version"],
        cfg["release"],
        "vm-bookworm",
        "build",
    )
    with open(build_dir / cfg["build_yml"]) as f:
        info = yaml.safe_load(f.read())

    assert info.get("orig", None) == cfg["orig"]
    assert cfg["package"] in info.get("packages", [])


def _get_template_timestamp(artifacts_dir, template_name, stage):
    assert (artifacts_dir / f"templates/{template_name}.{stage}.yml").exists()
    with open(artifacts_dir / f"templates/{template_name}.{stage}.yml") as f:
        data = yaml.safe_load(f.read())
    assert data.get("timestamp", None)
    return parsedate(data["timestamp"]).strftime("%Y%m%d%H%M")


#
# Pipeline for Fedora 43 template
#


def test_template_fedora_43_minimal_prep(artifacts_dir):
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
        FEDORA_MINIMAL,
        "template",
        "prep",
    )

    assert (artifacts_dir / f"templates/{FEDORA_MINIMAL}.prep.yml").exists()
    assert (
        artifacts_dir / f"templates/qubeized_images/{FEDORA_MINIMAL}/root.img"
    ).exists()
    assert (artifacts_dir / f"templates/{FEDORA_MINIMAL}/appmenus").exists()
    assert (
        artifacts_dir / f"templates/{FEDORA_MINIMAL}/template.conf"
    ).exists()


def test_template_fedora_43_minimal_build(artifacts_dir):
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-t",
        FEDORA_MINIMAL,
        "template",
        "build",
    )

    template_prep_timestamp = _get_template_timestamp(
        artifacts_dir, FEDORA_MINIMAL, "prep"
    )
    template_timestamp = _get_template_timestamp(
        artifacts_dir, FEDORA_MINIMAL, "build"
    )
    assert template_timestamp == template_prep_timestamp

    assert (
        artifacts_dir
        / f"templates/rpm/qubes-template-{FEDORA_MINIMAL}-4.2.0-{template_timestamp}.noarch.rpm"
    ).exists()


def test_template_fedora_43_minimal_sign(artifacts_dir):
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
            FEDORA_MINIMAL,
            "template",
            "sign",
            env=env,
        )

    dbpath = artifacts_dir / "templates/rpmdb"
    assert dbpath.exists()

    template_timestamp = _get_template_timestamp(
        artifacts_dir, FEDORA_MINIMAL, "build"
    )
    rpm_path = (
        artifacts_dir
        / f"templates/rpm/qubes-template-{FEDORA_MINIMAL}-4.2.0-{template_timestamp}.noarch.rpm"
    )
    assert rpm_path.exists()
    result = subprocess.run(
        f"rpm --dbpath {dbpath} -K {rpm_path}",
        check=True,
        capture_output=True,
        shell=True,
    )
    assert "digests signatures OK" in result.stdout.decode()


def test_template_fedora_43_minimal_publish(artifacts_dir):
    # Clean stale publish state from previous runs.
    stale_publish = artifacts_dir / f"templates/{FEDORA_MINIMAL}.publish.yml"
    if stale_publish.exists():
        stale_publish.unlink()
    stale_rpm_repo = artifacts_dir / "repository-publish/rpm/r4.2"
    for repo in ("templates-itl-testing", "templates-itl"):
        stale = stale_rpm_repo / repo
        if stale.exists():
            shutil.rmtree(stale)

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
            FEDORA_MINIMAL,
            "repository",
            "publish",
            "templates-itl-testing",
            env=env,
        )

        publish_file = artifacts_dir / f"templates/{FEDORA_MINIMAL}.publish.yml"
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        template_timestamp = _get_template_timestamp(
            artifacts_dir, FEDORA_MINIMAL, "build"
        )

        assert info.get("timestamp", []) == template_timestamp
        assert ["templates-itl-testing"] == [
            r["name"] for r in info.get("repository-publish", [])
        ]

        # publish into templates-itl
        fake_time = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=7)
        ).strftime("%Y%m%d%H%M")

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
            FEDORA_MINIMAL,
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
        rpm = f"qubes-template-{FEDORA_MINIMAL}-4.2.0-{template_timestamp}.noarch.rpm"
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
def test_template_fedora_43_minimal_publish_new(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        build_yml = artifacts_dir / f"templates/{FEDORA_MINIMAL}.build.yml"
        assert build_yml.exists()
        with open(build_yml) as f:
            data = yaml.safe_load(f.read())
        assert data.get("timestamp", None)
        template_timestamp = parsedate(data["timestamp"]).strftime("%Y%m%d%H%M")

        # bump timestamp, without re-running "prep" stage
        new_timestamp = (
            parsedate(data["timestamp"]) + datetime.timedelta(minutes=1)
        ).strftime("%Y%m%d%H%M")
        data["timestamp"] = new_timestamp
        with open(
            artifacts_dir / f"templates/{FEDORA_MINIMAL}.prep.yml", "w"
        ) as f:
            f.write(yaml.dump(data))

        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            FEDORA_MINIMAL,
            "template",
            "build",
            "sign",
            env=env,
        )

        assert (
            artifacts_dir
            / f"templates/rpm/qubes-template-{FEDORA_MINIMAL}-4.2.0-{new_timestamp}.noarch.rpm"
        ).exists()
        assert template_timestamp != new_timestamp

        # publish into templates-itl-testing
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            FEDORA_MINIMAL,
            "repository",
            "publish",
            "templates-itl-testing",
            env=env,
        )

        publish_file = artifacts_dir / f"templates/{FEDORA_MINIMAL}.publish.yml"
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
            FEDORA_MINIMAL,
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
            f"qubes-template-{FEDORA_MINIMAL}-4.2.0-{template_timestamp}.noarch.rpm",
            f"qubes-template-{FEDORA_MINIMAL}-4.2.0-{new_timestamp}.noarch.rpm",
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


def test_template_fedora_43_minimal_unpublish(artifacts_dir):
    env = os.environ.copy()
    with tempfile.TemporaryDirectory() as tmpdir:
        gnupghome = f"{tmpdir}/gnupg"
        shutil.copytree(PROJECT_PATH / "tests/gnupg", gnupghome)
        os.chmod(gnupghome, 0o700)

        env["GNUPGHOME"] = gnupghome
        env["HOME"] = tmpdir

        template_timestamp = _get_template_timestamp(
            artifacts_dir, FEDORA_MINIMAL, "build"
        )

        # unpublish from templates-itl
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            FEDORA_MINIMAL,
            "repository",
            "unpublish",
            "templates-itl",
            env=env,
        )

        publish_file = artifacts_dir / f"templates/{FEDORA_MINIMAL}.publish.yml"
        with open(publish_file) as f:
            info = yaml.safe_load(f.read())

        assert info.get("timestamp", []) == template_timestamp
        assert set([r["name"] for r in info.get("repository-publish", [])]) == {
            "templates-itl-testing"
        }

    # Check that packages are in the published repository
    for repository in ["templates-itl-testing", "templates-itl"]:
        rpm = f"qubes-template-{FEDORA_MINIMAL}-4.2.0-{template_timestamp}.noarch.rpm"
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
            artifacts_dir, FEDORA_MINIMAL, "build"
        )
        rpm = f"qubes-template-{FEDORA_MINIMAL}-4.2.0-{template_timestamp}.noarch.rpm"

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
                f"""
%packages
qubes-template-{FEDORA_MINIMAL}
%end
"""
            )

        # make ISO cache with a single template
        qb_call(
            DEFAULT_BUILDER_CONF,
            artifacts_dir,
            "-t",
            FEDORA_MINIMAL,
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


#
# Tests for init-cache
#


def test_init_cache_reuse_and_force_rpm(artifacts_dir):
    # Create cache
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "host-fc37",
        "package",
        "init-cache",
    )

    # Should reuse existing cache
    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "host-fc37",
        "package",
        "init-cache",
    ).decode()
    assert "Re-using existing cache" in output

    # Force recreation
    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "host-fc37",
        "package",
        "init-cache",
        "--force",
    ).decode()
    assert "Forcing cache recreation" in output


def test_init_cache_packages_diff_recreates_deb(artifacts_dir):
    # Create cache
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-bookworm",
        "package",
        "init-cache",
    )

    # Patch builder config to request an extra package
    new_conf = artifacts_dir / "builder-updated.yml"
    with open(DEFAULT_BUILDER_CONF) as f:
        config = yaml.safe_load(f)
    config.setdefault("cache", {}).setdefault("vm-bookworm", {})["packages"] = [
        "vim"
    ]
    with open(new_conf, "w") as f:
        yaml.safe_dump(config, f)

    # Run again: should detect package set difference
    output = qb_call_output(
        new_conf,
        artifacts_dir,
        "-d",
        "vm-bookworm",
        "package",
        "init-cache",
    ).decode()
    assert (
        "Existing packages in cache differ from requested ones. Recreating cache"
        in output
    )


def test_init_cache_reuse_and_force_arch(artifacts_dir):
    # Create cache
    qb_call(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-archlinux",
        "package",
        "init-cache",
    )

    # Must reuse cache
    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-archlinux",
        "package",
        "init-cache",
    ).decode()
    assert "Re-using existing cache" in output

    # Force recreation
    output = qb_call_output(
        DEFAULT_BUILDER_CONF,
        artifacts_dir,
        "-d",
        "vm-archlinux",
        "package",
        "init-cache",
        "--force",
    ).decode()
    assert "Forcing cache recreation" in output
