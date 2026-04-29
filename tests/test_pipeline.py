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

from qubesbuilder.cli.cli_package import _component_stage
from qubesbuilder.common import PROJECT_PATH
from qubesbuilder.config import Config

DEFAULT_BUILDER_CONF = PROJECT_PATH / "tests/builder-ci.yml"


def _make_config(tmpdir):
    cfg = Config(DEFAULT_BUILDER_CONF)
    cfg.set("artifacts-dir", str(tmpdir))
    return cfg


def test_fetch_runs_independently_per_component(tmp_path):
    # Regression: when two components share a Config object (as in the github
    # action multi-component build), the second component's fetch must not be
    # skipped because the first one already ran.
    cfg = _make_config(tmp_path)
    cfg.set("executor", {"type": "local"})
    cfg.set("skip-git-fetch", True)

    dists = cfg.get_distributions(filtered_distributions=["host-fc37"])
    comp_a = cfg.get_components(filtered_components=["example-advanced"])
    comp_b = cfg.get_components(filtered_components=["example-advanced-clone"])

    _component_stage(
        config=cfg,
        components=comp_a,
        distributions=dists,
        stages=["fetch"],
    )

    _component_stage(
        config=cfg,
        components=comp_b,
        distributions=dists,
        stages=["fetch"],
    )

    fetch_done = cfg.get("session-fetch-done", set())

    assert isinstance(
        fetch_done, set
    ), f"session-fetch-done should be a set, got {type(fetch_done).__name__}"
    assert (
        "example-advanced" in fetch_done
    ), "example-advanced not recorded in session-fetch-done"
    assert (
        "example-advanced-clone" in fetch_done
    ), "example-advanced-clone not recorded - fetch was blocked by first component"
