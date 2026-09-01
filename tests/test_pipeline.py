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

from qubesbuilder.cli.cli_package import (
    _all_package_stage,
    _component_stage,
)
from qubesbuilder.cli.cli_template import (
    _all_template_stage,
    _template_stage,
)
from qubesbuilder.common import PROJECT_PATH, STAGES
from types import SimpleNamespace

import click

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


def test_template_prep_skips_when_timestamp_is_passed(tmp_path):
    # Regression: the github action passes a template timestamp on every
    # stage call, and that alone used to force a full prep, rebuilding the
    # root image even though the artifacts were already there.
    cfg = _make_config(tmp_path)
    cfg.set("executor", {"type": "local"})

    templates = cfg.get_templates(["fedora-43-xfce"])
    pipeline = cfg.get_pipeline(
        components=[],
        distributions=[],
        templates=templates,
        installers=[],
        stages=["prep"],
    )
    prep = [j for j in pipeline.sorted_jobs(cfg) if j.stage == "prep"][0]

    templates_dir = cfg.templates_dir
    qubeized = templates_dir / "qubeized_images" / templates[0].name
    qubeized.mkdir(parents=True)
    (qubeized / "root.img").write_bytes(b"")
    (templates_dir / f"{templates[0]}.prep.yml").write_text(
        "timestamp: '202601010000'\n"
    )

    def fail(*args, **kwargs):
        raise AssertionError("prep must not run the executor")

    prep.executor.run = fail
    prep.run(template_timestamp="202601010000")


def _template_jobs(cfg, stages):
    pipeline = cfg.get_pipeline(
        components=[],
        distributions=[],
        templates=cfg.get_templates(["fedora-43-xfce"]),
        installers=[],
        stages=stages,
    )
    return [(job.stage, job.name) for job in pipeline.sorted_jobs(cfg)]


def test_template_upload_depends_on_nothing(tmp_path):
    # Upload was a stage of the template plugin, so it depended on publish
    # and pulled fetch, prep, build, sign and publish.
    cfg = _make_config(tmp_path)
    assert _template_jobs(cfg, ["upload"]) == [("upload", "upload")]


def test_template_upload_runs_last_in_a_full_pipeline(tmp_path):
    cfg = _make_config(tmp_path)
    assert _template_jobs(cfg, list(STAGES)) == [
        ("fetch", "fetch"),
        ("prep", "template"),
        ("build", "template"),
        ("sign", "template"),
        ("publish", "template"),
        ("upload", "upload"),
    ]


def test_package_upload_is_unaffected(tmp_path):
    cfg = _make_config(tmp_path)
    pipeline = cfg.get_pipeline(
        components=[],
        distributions=cfg.get_distributions(["host-fc37"]),
        templates=[],
        installers=[],
        stages=["upload"],
    )
    jobs = pipeline.sorted_jobs(cfg)
    assert [(j.stage, j.name) for j in jobs] == [("upload", "upload")]
    assert jobs[0].template is None


def test_template_upload_pushes_a_repository_with_nothing_published(tmp_path):
    # Nothing was built or published into templates-itl in this run: the
    # repository content is rsynced all the same.
    cfg = _make_config(tmp_path)
    cfg.set("executor", {"type": "local"})
    cfg.set("repository-publish", {"templates": "templates-itl"})
    cfg.set("repository-upload-remote-host", {"rpm": str(tmp_path / "remote")})
    local = cfg.repository_publish_dir / "rpm" / cfg.qubes_release
    (local / "templates-itl" / "repodata").mkdir(parents=True)

    _template_stage(
        config=cfg,
        templates=cfg.get_templates(["fedora-43-xfce"]),
        stages=["upload"],
    )

    assert (tmp_path / "remote" / "templates-itl" / "repodata").is_dir()


def test_template_upload_skips_when_no_repository_configured(tmp_path):
    # TemplateBuilderPlugin.matches used to drop the upload job when
    # 'repository-publish:templates' was unset. Skip cleanly rather than
    # failing on a missing repository name.
    cfg = _make_config(tmp_path)
    cfg.set("executor", {"type": "local"})
    cfg.set("repository-publish", {"components": "current-testing"})
    cfg.set("repository-upload-remote-host", {"rpm": str(tmp_path / "remote")})

    _template_stage(
        config=cfg,
        templates=cfg.get_templates(["fedora-43-xfce"]),
        stages=["upload"],
    )

    assert not (tmp_path / "remote").exists()


def test_all_stages_do_not_mutate_module_stages(tmp_path):
    # 'stages = STAGES' followed by stages.remove("upload") dropped 'upload'
    # from the module-level list, and so from the stage ordering in jobs.py,
    # for the rest of the process.
    cfg = _make_config(tmp_path)
    cfg.set("automatic-upload-on-publish", True)
    obj = SimpleNamespace(
        config=cfg, templates=[], components=[], distributions=[]
    )

    with click.Context(_all_template_stage, obj=obj):
        _all_template_stage.callback()
    assert "upload" in STAGES

    with click.Context(_all_package_stage, obj=obj):
        _all_package_stage.callback()
    assert "upload" in STAGES
    assert "upload" in cfg.get_stages()


def test_all_stages_survive_a_config_without_upload(tmp_path):
    # get_stages() is the user's configured list and need not have 'upload'.
    cfg = _make_config(tmp_path)
    cfg.set("automatic-upload-on-publish", True)
    cfg.set("stages", ["fetch", "prep", "build"])
    obj = SimpleNamespace(
        config=cfg, templates=[], components=[], distributions=[]
    )
    with click.Context(_all_package_stage, obj=obj):
        _all_package_stage.callback()


def test_component_stage_does_not_mutate_stages(tmp_path):
    cfg = _make_config(tmp_path)
    cfg.set("executor", {"type": "local"})
    cfg.set("skip-git-fetch", True)
    stages = ["fetch", "prep"]
    _component_stage(config=cfg, components=[], distributions=[], stages=stages)
    assert stages == ["fetch", "prep"]
