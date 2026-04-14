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

import pytest
import importlib

from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
import qubesbuilder.log as qb_log
import qubesbuilder.plugins as qb_plugins
from qubesbuilder.plugins.upload import UploadError, UploadPlugin


BUILDER_CONF = """\
executor:
  type: local
artifacts-dir: {artifacts_dir}
repository-publish:
  components: current-testing
"""


class DummyUnknownDist:
    distribution = "vm-custom"
    type = "custom"
    package_set = "vm"
    name = "custom"

    def is_rpm(self):
        return False

    def is_deb(self):
        return False

    def is_ubuntu(self):
        return False

    def is_archlinux(self):
        return False

    def is_windows(self):
        return False

    def __str__(self):
        return "dummy-custom"


@pytest.fixture
def config(tmp_path):
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    conf_file = tmp_path / "builder.yml"
    conf_file.write_text(BUILDER_CONF.format(artifacts_dir=artifacts_dir))
    return Config(conf_file)


@pytest.fixture(autouse=True)
def refresh_qb_logger(monkeypatch):
    """
    Tests reload logging module that makes QubesBuilderLogger
    referencing an incompatible logger class instance.
    Reload qubesbuilder.log and rebind the plugin module global
    to keep logger hierarchy creation stable across test files.
    """
    reloaded_log = importlib.reload(qb_log)
    monkeypatch.setattr(
        qb_plugins, "QubesBuilderLogger", reloaded_log.QubesBuilderLogger
    )


def _plugin(config, dist):
    return UploadPlugin(dist=dist, config=config, stage="upload")


@pytest.mark.parametrize(
    ("dist", "expected"),
    [
        (QubesDistribution("host-fc37"), True),
        (QubesDistribution("vm-bookworm"), True),
        (QubesDistribution("vm-archlinux"), True),
        (QubesDistribution("vm-win10"), True),
        (DummyUnknownDist(), False),
    ],
)
def test_supported_distribution(dist, expected):
    assert UploadPlugin.supported_distribution(dist) is expected


def test_run_requires_local_executor(config):
    plugin = _plugin(config, QubesDistribution("host-fc37"))
    plugin.executor = object()

    with pytest.raises(UploadError, match="only supports local executor"):
        plugin.run()


def test_run_skips_when_no_remote_path(config, monkeypatch):
    plugin = _plugin(config, QubesDistribution("host-fc37"))
    monkeypatch.setattr(
        plugin.executor,
        "run",
        lambda *args, **kwargs: pytest.fail(
            "executor.run should not be called"
        ),
    )

    plugin.run()


def test_run_rpm_upload_path(config, monkeypatch):
    config.set("repository-upload-remote-host", {"rpm": "/remote/rpm/r4.2"})
    plugin = _plugin(config, QubesDistribution("host-fc37"))

    commands = []
    monkeypatch.setattr(
        plugin.executor, "run", lambda cmd: commands.append(cmd)
    )

    plugin.run(repository_publish="unstable")

    assert len(commands) == 1
    assert "repository-publish/rpm/r4.2/unstable/host/fc37/" in commands[0][0]
    assert "/remote/rpm/r4.2/unstable/host/fc37/" in commands[0][0]


def test_run_deb_upload_paths(config, monkeypatch):
    config.set("repository-upload-remote-host", {"deb": "/remote/deb/r4.2"})
    plugin = _plugin(config, QubesDistribution("vm-bookworm"))

    monkeypatch.setattr(
        "qubesbuilder.plugins.upload.DEBRepoPlugin.get_debian_suite_from_repository_publish",
        lambda dist, repository_publish: "bookworm-testing",
    )

    commands = []
    monkeypatch.setattr(
        plugin.executor, "run", lambda cmd: commands.append(cmd)
    )

    plugin.run(repository_publish="current-testing")

    assert len(commands) == 2
    assert "repository-publish/deb/r4.2/vm/pool/" in commands[0][0]
    assert "/remote/deb/r4.2/vm/pool/" in commands[0][0]
    assert (
        "repository-publish/deb/r4.2/vm/dists/bookworm-testing/"
        in commands[1][0]
    )
    assert "/remote/deb/r4.2/vm/dists/bookworm-testing/" in commands[1][0]


def test_run_windows_uses_default_repository_publish(config, monkeypatch):
    config.set(
        "repository-upload-remote-host", {"windows": "/remote/windows/r4.2"}
    )
    config.set("repository-publish", {"components": "security-testing"})
    plugin = _plugin(config, QubesDistribution("vm-win10"))

    commands = []
    monkeypatch.setattr(
        plugin.executor, "run", lambda cmd: commands.append(cmd)
    )

    plugin.run()

    assert len(commands) == 1
    assert (
        "repository-publish/windows/r4.2/security-testing/vm/win10/"
        in commands[0][0]
    )
    assert "/remote/windows/r4.2/security-testing/vm/win10/" in commands[0][0]


def test_run_cannot_determine_directories(config):
    config.set("repository-upload-remote-host", {"custom": "/remote/custom"})
    plugin = _plugin(config, DummyUnknownDist())

    with pytest.raises(
        UploadError, match="Cannot determine directories to upload"
    ):
        plugin.run(repository_publish="unstable")


def test_run_wraps_executor_error(config, monkeypatch):
    config.set("repository-upload-remote-host", {"rpm": "/remote/rpm/r4.2"})
    plugin = _plugin(config, QubesDistribution("host-fc37"))

    def raise_executor_error(*args, **kwargs):
        raise ExecutorError("rsync-failed")

    monkeypatch.setattr(plugin.executor, "run", raise_executor_error)

    with pytest.raises(UploadError, match="Failed to upload to remote host"):
        plugin.run(repository_publish="unstable")
