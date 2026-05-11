# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2026 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import subprocess

import pytest
import yaml

from qubesbuilder.cli.cli_list_deps import (
    DEFAULT_EXCLUDES,
    SingleQuoted,
    _normalize_for_cache,
    is_safe_dep,
)
from qubesbuilder.common import PROJECT_PATH
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.plugins.list_deps_archlinux import ArchLinuxListDepsPlugin
from qubesbuilder.plugins.list_deps_deb import DEBListDepsPlugin
from qubesbuilder.plugins.list_deps_rpm import RPMListDepsPlugin


BUILDER_CONF = """\
executor:
  type: local
artifacts-dir: {artifacts_dir}
components:
  - {component}
distributions:
  - {dist}
"""


def _make_artifact(artifacts_dir, component, version, dist, build, deps):
    stage_dir = (
        artifacts_dir / "components" / component / version / dist / "list-deps"
    )
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / f"{build}.list-deps.yml").write_text(
        yaml.safe_dump(
            {"build": build, "build-deps": deps, "source-hash": "toto"}
        )
    )


def _qb(conf, *args):
    """Run qb and return (returncode, stdout)."""
    cmd = [
        "python3",
        str(PROJECT_PATH / "qb"),
        "--builder-conf",
        str(conf),
        *args,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout


@pytest.mark.parametrize(
    "line",
    [
        "gcc",
        "gcc >= 11",
        "Thunar-devel >= 1.8.0",
        "/etc/fedora-release",
        "foo~rc1",
    ],
)
def test_is_safe_dep_accepts_legitimate_tokens(line):
    assert is_safe_dep(line)


@pytest.mark.parametrize(
    "line",
    [
        "gcc; rm -rf /",
        "rm -rf /",
        "../../../etc/shadow",
        "foo/../bar",
        "perl(File::Find)",
        "pkgconfig(libsystemd) >= 209",
        "foo|bar",
        "foo$bar",
        "foo`bar`",
        "pkg\twith\ttabs",
        "pkg\nwith\nnewline",
        "paquet-\xe9clair",
        "a" * 101,
        "",
    ],
)
def test_is_safe_dep_rejects_dangerous_tokens(line):
    assert not is_safe_dep(line)


def test_normalize_drops_boolean_expressions():
    out = _normalize_for_cache(["((A or B) with C)", "(A if B)", "gcc"])
    assert out == ["gcc"]


def test_normalize_dedupes_bare_versus_versioned():
    out = _normalize_for_cache(["gcc", "gcc >= 11"])
    assert out == ["gcc >= 11"]


def test_normalize_keeps_range_bounds_separately():
    out = _normalize_for_cache(
        [
            "pulseaudio-libs-devel <= 17.0",
            "pulseaudio-libs-devel >= 0.9.21",
        ]
    )
    assert out == [
        "pulseaudio-libs-devel <= 17.0",
        "pulseaudio-libs-devel >= 0.9.21",
    ]


def test_normalize_drops_unsafe_tokens():
    out = _normalize_for_cache(["gcc", "evil; rm -rf /"])
    assert out == ["gcc"]


def test_normalize_preserves_file_provides():
    out = _normalize_for_cache(["/etc/fedora-release", "/usr/bin/perl"])
    assert out == ["/etc/fedora-release", "/usr/bin/perl"]


def test_normalize_skips_blank_and_comment_lines():
    out = _normalize_for_cache(["", "  ", "# comment", "gcc"])
    assert out == ["gcc"]


def test_normalize_excludes_by_regex():
    out = _normalize_for_cache(
        ["gcc", "qubes-vmm-xen-devel", "qubes-gpg-split >= 2.0"],
        excludes=["^qubes-"],
    )
    assert out == ["gcc"]


def test_normalize_multiple_exclude_patterns():
    out = _normalize_for_cache(
        ["gcc", "qubes-foo", "xen-libs", "python3-bar"],
        excludes=["^qubes-", "^xen-"],
    )
    assert out == ["gcc", "python3-bar"]


def test_normalize_empty_excludes_is_noop():
    out = _normalize_for_cache(["gcc", "qubes-foo"], excludes=[])
    assert out == ["gcc", "qubes-foo"]


def test_default_excludes_drops_qubes_prefix():
    assert DEFAULT_EXCLUDES == ("^qubes-",)


def test_single_quoted_emits_with_single_quotes():
    text = yaml.safe_dump(
        {"packages": [SingleQuoted("gcc"), SingleQuoted("xen-devel >= 4.2")]},
        default_flow_style=False,
    )
    assert "- 'gcc'\n" in text
    assert "- 'xen-devel >= 4.2'\n" in text


def test_plain_str_falls_through_to_default_style():
    text = yaml.safe_dump(
        {"packages": ["gcc", "xen-devel >= 4.2"]},
        default_flow_style=False,
    )
    assert "- gcc\n" in text
    assert "- xen-devel >= 4.2\n" in text


@pytest.mark.parametrize(
    ("plugin_cls", "dist", "expected"),
    [
        (RPMListDepsPlugin, QubesDistribution("host-fc41"), True),
        (RPMListDepsPlugin, QubesDistribution("vm-bookworm"), False),
        (DEBListDepsPlugin, QubesDistribution("vm-bookworm"), True),
        (DEBListDepsPlugin, QubesDistribution("host-fc41"), False),
        (ArchLinuxListDepsPlugin, QubesDistribution("vm-archlinux"), True),
        (ArchLinuxListDepsPlugin, QubesDistribution("host-fc41"), False),
    ],
)
def test_dist_filter_matches_correct_plugin(plugin_cls, dist, expected):
    assert plugin_cls.dist_filter(dist) is expected


def _provide_component(artifacts_dir, component, version="4.2.8", release="1"):
    src = artifacts_dir / "sources" / component
    src.mkdir(parents=True, exist_ok=True)
    (src / "version").write_text(f"{version}\n")
    (src / "rel").write_text(f"{release}\n")
    return f"{version}-{release}"


def test_cli_show_emits_aggregated_cache_yaml(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "xen-devel >= 4.2"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )

    rc, out = _qb(conf, "list-deps", "show")
    assert rc == 0, out
    data = yaml.safe_load(out)
    assert data == {
        "cache": {"host-fc41": {"packages": ["gcc", "xen-devel >= 4.2"]}}
    }


def test_cli_show_merges_multiple_builds(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "xen-devel >= 4.2"],
    )
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan-devel.spec",
        ["glibc-devel", "xen-devel >= 4.2"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )

    rc, out = _qb(conf, "list-deps", "show")
    assert rc == 0, out
    pkgs = yaml.safe_load(out)["cache"]["host-fc41"]["packages"]
    assert "gcc" in pkgs
    assert "glibc-devel" in pkgs
    assert "xen-devel >= 4.2" in pkgs


def test_cli_show_no_artifacts_emits_empty_cache(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _provide_component(artifacts_dir, "core-vchan-xen")
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )

    rc, out = _qb(conf, "list-deps", "show")
    assert rc == 0, out
    data = yaml.safe_load(out)
    assert data == {"cache": {}}


def test_cli_update_merges_and_quotes_packages(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "xen-devel >= 4.2"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )
    target = tmp_path / "target.yml"
    target.write_text(
        "distributions:\n- host-fc41\n"
        "cache:\n  host-fc41:\n    packages:\n      - preexisting-pkg\n"
    )

    rc, _ = _qb(conf, "list-deps", "update", str(target))
    assert rc == 0

    raw = target.read_text()
    assert "- 'gcc'" in raw
    assert "- 'xen-devel >= 4.2'" in raw
    assert "- 'preexisting-pkg'" in raw
    # Non-cache section left intact (plain list, no quoting).
    assert "- host-fc41" in raw
    assert (tmp_path / "target.yml.bak").exists()


def test_cli_update_is_idempotent(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "xen-devel >= 4.2"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )
    target = tmp_path / "target.yml"
    target.write_text("distributions:\n- host-fc41\n")

    _qb(conf, "list-deps", "update", str(target))
    after_first = target.read_text()
    _qb(conf, "list-deps", "update", str(target))
    after_second = target.read_text()

    assert after_first == after_second


def test_cli_show_default_excludes_qubes_prefix(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "qubes-vmm-xen-devel", "qubes-gpg-split >= 2.0"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )

    rc, out = _qb(conf, "list-deps", "show")
    assert rc == 0, out
    pkgs = yaml.safe_load(out)["cache"]["host-fc41"]["packages"]
    assert pkgs == ["gcc"]


def test_cli_show_exclude_flag_extends_default(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "qubes-foo", "xen-libs"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )

    rc, out = _qb(conf, "list-deps", "show", "--exclude", "^xen-")
    assert rc == 0, out
    pkgs = yaml.safe_load(out)["cache"]["host-fc41"]["packages"]
    assert pkgs == ["gcc"]


def test_cli_show_builder_conf_exclude_overrides_default(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    verrel = _provide_component(artifacts_dir, "core-vchan-xen")
    _make_artifact(
        artifacts_dir,
        "core-vchan-xen",
        verrel,
        "host-fc41",
        "rpm_spec_libvchan.spec",
        ["gcc", "qubes-foo"],
    )
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
        + "list-deps:\n  exclude: []\n"
    )

    rc, out = _qb(conf, "list-deps", "show")
    assert rc == 0, out
    pkgs = yaml.safe_load(out)["cache"]["host-fc41"]["packages"]
    assert pkgs == ["gcc", "qubes-foo"]


def test_cli_update_errors_on_missing_target(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    conf = tmp_path / "builder.yml"
    conf.write_text(
        BUILDER_CONF.format(
            artifacts_dir=artifacts_dir,
            component="core-vchan-xen",
            dist="host-fc41",
        )
    )

    rc, _ = _qb(conf, "list-deps", "update", str(tmp_path / "no-such.yml"))
    assert rc != 0
