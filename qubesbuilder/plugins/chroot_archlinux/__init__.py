# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2023 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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
from qubesbuilder.executors import ExecutorError
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.plugins import ArchlinuxDistributionPlugin
from qubesbuilder.plugins.chroot import ChrootError, ChrootPlugin


def get_pacman_cmd(
    gen_path,
    conf_template,
    conf,
    servers=None,
    enable_builder_local=False,
    use_qubes_repo_version=None,
    use_qubes_repo_testing=False,
):
    # Prepare generate-pacman args
    gen_pacman_cmd = [
        "sudo",
        gen_path,
        conf_template,
        conf,
    ]
    for server in servers or []:
        gen_pacman_cmd += ["--server", server.rstrip("/")]

    # Host (cage) has access to builder-local repository that will be mounted bind into
    # the archlinux chroot. Configurations will be copied-in by qubes-x86_64-build (archbuild).
    if enable_builder_local:
        gen_pacman_cmd.append("--enable-builder-local")

    if use_qubes_repo_version:
        gen_pacman_cmd += [
            "--use-qubes-repo-version",
            str(use_qubes_repo_version),
        ]
        if use_qubes_repo_testing:
            gen_pacman_cmd.append("--use-qubes-repo-testing")

    return [" ".join(gen_pacman_cmd)]


def get_archchroot_cmd(
    chroot_dir, pacman_conf, makepkg_conf, additional_packages=None
):
    additional_packages = additional_packages or []

    mkarchchroot_cmd = [
        "sudo",
        "--preserve-env=GPG_TTY",
        "mkarchroot",
        "-C",
        str(pacman_conf),
        "-M",
        str(makepkg_conf),
        str(chroot_dir),
        "base-devel",
    ] + additional_packages

    cmd = [
        "sudo rm -rf /etc/pacman.d/gnupg/private-keys-v1.d",
        "sudo pacman-key --init",
        "sudo pacman-key --populate",
        f"sudo mkdir -p {chroot_dir.parent}",
        "export GPG_TTY=$(tty)",
        " ".join(mkarchchroot_cmd) + " <<< $(GPG_TTY)",
    ]

    return cmd


class ArchlinuxChrootPlugin(ArchlinuxDistributionPlugin, ChrootPlugin):
    """
    ArchlinuxChrootPlugin manages Archlinux chroot creation

    Stages:
        - chroot - Create Archlinux cache chroot.
    """

    name = "chroot_archlinux"
    stages = ["init-cache"]

    def __init__(
        self,
        dist: QubesDistribution,
        config: Config,
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(dist=dist, config=config, manager=manager)

    def run(self, stage: str):
        """
        Run plugin for given stage.
        """

        if stage != "init-cache":
            return

        executor = self.get_executor_from_config(stage)

        cache_chroot_dir = self.get_cache_dir() / "chroot" / self.dist.name
        cache_chroot_dir.mkdir(exist_ok=True, parents=True)

        chroot_name = "root"
        chroot_archive = f"{chroot_name}.tar.gz"
        (cache_chroot_dir / chroot_archive).unlink(missing_ok=True)

        copy_in = self.default_copy_in(
            executor.get_plugins_dir(), executor.get_sources_dir()
        )
        self.environment.update(
            {
                "DIST": self.dist.name,
                "PACKAGE_SET": self.dist.package_set,
                "TERM": "dumb"
            }
        )
        copy_out = [
            (
                executor.get_cache_dir() / chroot_archive,
                cache_chroot_dir,
            )
        ]

        pacman_conf_template = (
            f"{executor.get_plugins_dir()}/chroot_archlinux/conf/pacman.conf.j2"
        )

        pacman_conf = (
            "/usr/local/share/devtools/pacman.conf.d/qubes-x86_64.conf"
        )

        makepkg_conf = f"{executor.get_plugins_dir()}/chroot_archlinux/conf/makepkg-x86_64.conf"

        additional_packages = (
            self.config.get("cache", {})
            .get(self.dist.distribution, {})
            .get("packages", [])
        )

        servers = self.config.get("mirrors", {}).get(
            self.dist.distribution, []
        ) or self.config.get("mirrors", {}).get(self.dist.name, [])

        pacman_cmd = get_pacman_cmd(
            gen_path=f"{executor.get_plugins_dir()}/chroot_archlinux/scripts/generate-pacman",
            conf_template=pacman_conf_template,
            conf=pacman_conf,
            servers=servers,
        )

        chroot_dir = executor.get_cache_dir() / chroot_name

        cmd = pacman_cmd + get_archchroot_cmd(
            chroot_dir,
            pacman_conf,
            makepkg_conf,
            additional_packages=additional_packages,
        )

        cmd += [
            f"cd {executor.get_cache_dir()}",
            f"sudo tar cf {chroot_archive} {chroot_name}",
        ]

        try:
            executor.run(
                cmd,
                copy_in,
                copy_out,
                environment=self.environment,
            )
        except ExecutorError as e:
            msg = f"{self.dist}: Failed to generate chroot: {str(e)}."
            raise ChrootError(msg) from e


PLUGINS = [ArchlinuxChrootPlugin]
