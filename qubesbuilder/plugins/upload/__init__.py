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
from typing import Optional

from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors.local import LocalExecutor, ExecutorError
from qubesbuilder.plugins import (
    DistributionPlugin,
    PluginError,
    JobDependency,
    JobReference,
)
from qubesbuilder.plugins.publish_deb import DEBRepoPlugin


class UploadError(PluginError):
    pass


class UploadPlugin(DistributionPlugin):
    """
    UploadPlugin manages generic distribution upload.

    Stages:
        - upload - Upload published repository for given distribution to remote mirror.
    """

    name = "upload"
    stages = ["upload"]

    def __init__(
        self,
        dist: QubesDistribution,
        config: Config,
        stage: str,
        **kwargs,
    ):
        super().__init__(config=config, dist=dist, stage=stage, **kwargs)

    @classmethod
    def supported_distribution(cls, distribution: QubesDistribution):
        return (
            distribution.is_rpm()
            or distribution.is_deb()
            or distribution.is_ubuntu()
            or distribution.is_archlinux()
        )

    def run(self, repository_publish: Optional[str] = None, **kwargs):
        if not isinstance(self.executor, LocalExecutor):
            raise UploadError("This plugin only supports local executor.")

        remote_path = self.config.repository_upload_remote_host.get(
            self.dist.type, None
        )
        if not remote_path:
            self.log.info(f"{self.dist}: No remote location defined. Skipping.")
            return

        repository_publish = (
            repository_publish
            or self.config.repository_publish.get(
                "components", "current-testing"
            )
        )

        try:
            local_path = (
                self.config.repository_publish_dir
                / self.dist.type
                / self.config.qubes_release
            )
            # Repository dir relative to local path that will be the same on remote host
            directories_to_upload = []
            if self.dist.is_rpm() or self.dist.is_archlinux():
                directories_to_upload.append(
                    f"{repository_publish}/{self.dist.package_set}/{self.dist.name}"
                )
            elif self.dist.is_deb() or self.dist.is_ubuntu():
                debian_suite = (
                    DEBRepoPlugin.get_debian_suite_from_repository_publish(
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
                    f"rsync --partial --progress --hard-links -OJair --mkpath -- {local_path / relative_dir}/ {remote_path}/{relative_dir}/"
                ]
                self.executor.run(cmd)
        except ExecutorError as e:
            raise UploadError(
                f"{self.dist}: Failed to upload to remote host: {str(e)}"
            ) from e


PLUGINS = [UploadPlugin]
