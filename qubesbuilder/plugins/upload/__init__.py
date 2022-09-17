# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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

from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors.local import LocalExecutor, ExecutorError
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.log import get_logger
from qubesbuilder.plugins import DistributionPlugin, PluginError
from qubesbuilder.plugins.publish_deb import DEBPublishPlugin

log = get_logger("upload")


class UploadError(PluginError):
    pass


class UploadPlugin(DistributionPlugin):
    """
    UploadPlugin manages generic distribution upload.

    Stages:
        - upload - Upload published repository for given distribution to remote mirror.
    """

    stages = ["upload"]

    def __init__(
        self,
        dist: QubesDistribution,
        config: Config,
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(config=config, manager=manager, dist=dist)

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return distribution.is_rpm() or distribution.is_deb()

    def run(self, stage: str, repository_publish: str = None):
        if stage != "upload":
            return

        executor = self.config.get_executor_from_config(stage)

        if not isinstance(executor, LocalExecutor):
            raise UploadError("This plugin only supports local executor.")

        remote_path = self.config.repository_upload_remote_host.get(
            self.dist.type, None
        )
        if not remote_path:
            log.info(f"{self.dist}: No remote location defined. Skipping.")
            return

        repository_publish = repository_publish or self.config.repository_publish.get(
            "components", "current-testing"
        )

        try:
            local_path = (
                self.get_repository_publish_dir()
                / self.dist.type
                / self.config.qubes_release
            )
            # Repository dir relative to local path that will be the same on remote host
            directories_to_upload = []
            if self.dist.is_rpm():
                directories_to_upload.append(
                    f"{repository_publish}/{self.dist.package_set}/{self.dist.name}"
                )
            elif self.dist.is_deb():
                debian_suite = (
                    DEBPublishPlugin.get_debian_suite_from_repository_publish(
                        self.dist, repository_publish
                    )
                )
                directories_to_upload.append(f"{self.dist.package_set}/pool")
                directories_to_upload.append(
                    f"{self.dist.package_set}/dists/{debian_suite}"
                )

            if not directories_to_upload:
                raise UploadError(
                    f"{self.dist}: Cannot determine directories to upload."
                )

            for relative_dir in directories_to_upload:
                cmd = [
                    f"rsync --partial --progress --hard-links -air --mkpath -- {local_path / relative_dir}/ {remote_path}/{relative_dir}/"
                ]
                executor.run(cmd)
        except ExecutorError as e:
            raise UploadError(
                f"{self.dist}: Failed to upload to remote host: {str(e)}"
            ) from e


PLUGINS = [UploadPlugin]
