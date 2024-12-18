# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
# Copyright (C) 2024 Rafał Wojdyła <omeg@invisiblethingslab.com>
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
import logging
import os.path
import re
import shutil
import subprocess
import yaml
from enum import StrEnum
from pathlib import Path
from typing import Dict, List

from qubesadmin import Qubes
from qubesadmin.exc import QubesException
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.executors.windows import WindowsExecutor
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.plugins import WindowsDistributionPlugin, PluginDependency
from qubesbuilder.plugins.build import BuildPlugin, BuildError


class WinArtifactKind(StrEnum):
    BIN = "bin"
    INC = "inc"
    LIB = "lib"

    def __repr__(self) -> str:
        return self.name


yaml.SafeDumper.add_representer(WinArtifactKind, yaml.representer.SafeRepresenter.represent_str)


class WinArtifactSet:
    def __init__(self, artifacts: Dict[WinArtifactKind, List[Path]] = None):
        self.artifacts = artifacts or {kind: [] for kind in WinArtifactKind}

    def __repr__(self) -> str:
        return self.items().__repr__()

    def items(self) -> Dict[WinArtifactKind, List[Path]]:
        return self.artifacts.items()

    def add(self, kind: WinArtifactKind, file: Path):
        self.artifacts[kind] += [file]

    def get_kind(self, kind: WinArtifactKind) -> List[Path]:
        return self.artifacts[kind]


def clean_local_repository(
    log: logging.Logger,
    repository_dir: Path,
    component: QubesComponent,
    dist: QubesDistribution,
    all_versions: bool = False,
):
    """
    Remove component from local repository.
    """
    log.info(
        f"{component}:{dist}: Cleaning local repository '{repository_dir}'"
        f"{' (all versions)' if all_versions else ''}."
    )
    if all_versions:
        for version_dir in repository_dir.glob(f"{component.name}_*"):
            shutil.rmtree(version_dir.as_posix())
    else:
        target_dir = repository_dir / f"{component.name}_{component.version}"
        if target_dir.exists():
            shutil.rmtree(target_dir.as_posix())


def provision_local_repository(
    log: logging.Logger,
    repository_dir: Path,
    component: QubesComponent,
    dist: QubesDistribution,
    target: str,
    artifacts: WinArtifactSet,
    build_artifacts_dir: Path,
    test_sign: bool,
):
    """
    Provision local builder repository.
    """
    log.info(f"{component}:{dist}:{target}: Provisioning local repository '{repository_dir}'.")

    target_dir = repository_dir / f"{component.name}_{component.version}"
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        for kind in WinArtifactKind:
            (target_dir / kind).mkdir(parents=True, exist_ok=True)
            for file in artifacts.get_kind(kind):
                pkg_path = build_artifacts_dir / kind / file
                target_path = target_dir / kind / file
                os.link(pkg_path, target_path)
        if test_sign:
            target_path = target_dir / "sign.crt"
            src_path = build_artifacts_dir / "sign.crt"
            os.link(src_path, target_path)

    except (ValueError, PermissionError, NotImplementedError, FileExistsError) as e:
        msg = f"{component}:{dist}:{target}: Failed to provision local repository."
        raise BuildError(msg) from e


def mangle_key_name(key_name: str) -> str:
        return key_name.replace(" ", "__")


class WindowsBuildPlugin(WindowsDistributionPlugin, BuildPlugin):
    """
    WindowsBuildPlugin manages Windows distribution build.

    Stages:
        - build - Build VS solutions and provision local repository.

    Entry points:
        - build
    """

    name = "build_windows"
    stages = ["build"]
    dependencies = [PluginDependency("build")]

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config: Config,
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(component=component, dist=dist, config=config, manager=manager)
        self.app = Qubes()

    def update_parameters(self, stage: str):
        super().update_parameters(stage)

        # Set and update parameters based on top-level "source",
        # per package set and per distribution
        parameters = self.component.get_parameters(self.get_placeholders(stage))
        self._parameters.update(parameters.get(self.dist.package_set, {}).get("source", {}))
        self._parameters.update(parameters.get(self.dist.distribution, {}).get("source", {}))

    def update_placeholders(self, stage: str):
        super().update_placeholders(stage)
        stage_options = self.get_config_stage_options(stage)
        self._placeholders[stage].update(
            {
                "@CONFIGURATION@": stage_options.get("configuration", "Release"),
            }
        )

    def run_rpc_service(
        self,
        target: str,
        service: str,
        description: str,
        stdin: bytes = b"",
        check_return: bool = True,
    ) -> bytes:
        try:
            proc = self.app.run_service(
                target,
                service,
            )

            stdout, stderr = proc.communicate(stdin)
            if check_return and proc.returncode != 0:
                msg = f"Failed to {description}.\n"
                msg += stderr.decode("utf-8")
                raise BuildError(f"{self.component}:{self.dist}: " + msg)
        except QubesException as e:
            msg = f"Failed to {description}: failed to run service '{service}' in qube '{qube}'."
            raise BuildError(f"{self.component}:{self.dist}: " + msg) from e

        return stdout

    # Generate self-signed key if test-signing, get public cert
    def sign_prep(self, qube: str, key_name: str, test_sign: bool) -> str:
        if test_sign:
            self.log.debug(f"creating key '{key_name}' in qube '{qube}'")
            self.run_rpc_service(
                target=qube,
                service=f"qubes.WinSign.CreateKey+{mangle_key_name(key_name)}",
                description=f"create signing key '{key_name}'",
            )

        return self.run_rpc_service(
            target=qube,
            service=f"qubes.WinSign.GetCert+{mangle_key_name(key_name)}",
            description=f"get certificate for signing key '{key_name}'",
        )

    # Sign a file and return signed bytes
    def sign_sign(self, qube: str, key_name: str, file: Path) -> bytes:
        self.log.debug(f"signing '{file}' with '{key_name}'")
        with open(file, "rb") as f:
            return self.run_rpc_service(
                target=qube,
                service=f"qubes.WinSign.Sign+{mangle_key_name(key_name)}",
                description=f"sign '{file}' with key '{key_name}'",
                stdin=f.read(),
            )

    # Delete signing key
    def sign_delete_key(self, qube: str, key_name: str):
        out = self.run_rpc_service(
            target=qube,
            service=f"qubes.WinSign.QueryKey+{mangle_key_name(key_name)}",
            description=f"query signing key '{key_name}'",
            check_return=False,
        )

        if f"Key '{mangle_key_name(key_name)}' exists" not in out.decode("utf-8"):
            self.log.debug(f"key '{key_name}' does not exist")
            return

        self.log.debug(f"deleting key '{key_name}' in qube '{qube}'")
        self.run_rpc_service(
            target=qube,
            service=f"qubes.WinSign.DeleteKey+{mangle_key_name(key_name)}",
            description=f"delete signing key '{key_name}'",
        )

    def run(self, stage: str):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run(stage=stage)
        self.log.debug(f"run start for {self.component.name}")

        if stage != "build" or not self.has_component_packages("build"):
            return

        executor = self.get_executor_from_config(stage)
        if not isinstance(executor, WindowsExecutor):
            raise BuildError(f"Plugin {self.name} requires WindowsExecutor, got {executor.__class__.__name__}")

        parameters = self.get_parameters(stage)
        distfiles_dir = self.get_component_distfiles_dir()
        artifacts_dir = self.get_dist_component_artifacts_dir(stage)

        self.log.debug(f"{parameters=}")
        stage_options = self.get_config_stage_options(stage)
        self.log.debug(f"{stage_options=}")

        # Compare previous artifacts hash with current source hash
        hash = self.get_dist_artifacts_info(stage, self.component.name).get("source-hash", None)
        if self.component.get_source_hash() == hash:
            self.log.info(
                f"{self.component}:{self.dist}: Source hash is the same than already built source. Skipping."
            )
            return

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        # Create output folders
        output_dirs = { kind.value: artifacts_dir / kind.value for kind in WinArtifactKind }
        for dir in output_dirs.values():
            dir.mkdir(parents=True)

        # Source artifacts
        prep_artifacts_dir = self.get_dist_component_artifacts_dir(stage="prep")

        # Local build repository
        repository_dir = self.config.repository_dir / self.dist.distribution
        repository_dir.mkdir(parents=True, exist_ok=True)

        # Remove previous versions in order to keep the latest one only
        clean_local_repository(self.log, repository_dir, self.component, self.dist, True)

        # The Windows vm is not a true disposable so clean builder files there
        builder_dir = str(executor.get_builder_dir())
        executor.run([f"if exist \"{builder_dir}\" rmdir /s /q \"{builder_dir}\""])
        # sometimes the deletion seems to fail due to files being in use
        # (probably if build is re-run too fast)
        # the above command doesn't fail in this case for ~reasons~ so we double-check
        # TODO: meaningful error message or restart the VM
        executor.run([f"if exist \"{builder_dir}\" exit 1"])

        artifacts = WinArtifactSet()

        # Read information from source stage
        source_info = self.get_dist_artifacts_info(stage="prep", basename=self.component.name)

        # Authenticode signing prep
        test_sign = stage_options.get("test-sign", True)
        sign_qube = stage_options.get("sign-qube")
        if not sign_qube:
            raise BuilderError("'sign-qube' option not configured")
        sign_key_name = stage_options.get("sign-key-name", "Qubes Windows Tools")

        try:
            sign_cert = self.sign_prep(
                qube=sign_qube,
                key_name=sign_key_name,
                test_sign=test_sign,
            )

            with open(artifacts_dir / "sign.crt", "wb") as f:
                f.write(sign_cert)

            dvm = executor.create_dispvm()
            dvm.start()

            for target in parameters["build"]:
                self.log.debug(f"building {target}")

                # TODO: better mark that there's no target
                do_build = str(target) != "dummy"
                if do_build and target.suffix != ".sln":
                    raise BuildError(f"Plugin {self.name} can only build Visual Studio .sln targets")

                # Copy-in distfiles, source and dependencies repository
                copy_in = self.default_copy_in(executor.get_plugins_dir(), executor.get_sources_dir())
                copy_in += [
                    (repository_dir, executor.get_repository_dir()),  # deps
                    (self.component.source_dir, executor.get_build_dir()),
                    (distfiles_dir, executor.get_distfiles_dir()),
                ]

                copy_out = []

                # Parse output files
                for kind, dir in output_dirs.items():
                    files = parameters.get(kind, [])
                    for file in files:
                        copy_out += [(executor.get_build_dir() / self.component.name / file, dir)]
                        artifacts.add(kind, Path(file).name)

                if do_build:
                    # create component-local link to distfiles, local build compatibility
                    cmd = [
                        "mklink",
                        "/d",
                        str(executor.get_build_dir() / self.component.name / ".distfiles"),
                        str(executor.get_distfiles_dir() / self.component.name),
                    ]

                    cmds = [" ".join(cmd)]
                    cmd = [
                        "powershell",
                        "-noninteractive",
                        "-executionpolicy", "bypass",
                        f"{ executor.get_plugins_dir() / self.name / 'scripts' / 'build-sln.ps1' }",
                        "-solution", str(executor.get_build_dir() / self.component.name / target),
                        "-repo", str(executor.get_repository_dir() / self.dist.distribution),
                        "-distfiles", str(executor.get_distfiles_dir() / self.component.name),
                        "-testsign", "$true" if test_sign else "$false",
                    ]

                    cmd += ["-configuration", self._placeholders[stage]["@CONFIGURATION@"]]

                    if self.config.debug:
                        cmd += ["-log"]  # generate msbuild log

                    if self.config.verbose:
                        cmd += ["-noisy"]

                    if executor.get_threads() > 1:
                        cmd += ["-threads", str(executor.get_threads())]
                    cmds += [" ".join(cmd)]
                else:  # dummy
                    cmds = ["exit 0"]

                # TODO: failed builds don't get caught here due to msbuild/powershell weirdness
                # see scripts/build-sln.ps1
                # this is only a problem if a target has no outputs (then copy_out fails)
                try:
                    executor.run(
                        cmds,
                        copy_in,
                        copy_out,
                    )
                except ExecutorError as e:
                    msg = f"{self.component}:{self.dist}:{target}: Failed to build solution: {str(e)}."
                    raise BuildError(msg) from e

                # authenticode sign the binaries
                skip_test_sign = parameters.get("skip-test-sign", [])
                for file in artifacts.get_kind(WinArtifactKind.BIN):
                    if not Path(file).suffix in [".cat", ".dll", ".exe", ".sys"]:
                        continue
                    if test_sign and file in skip_test_sign:
                        continue

                    path = artifacts_dir / "bin" / file
                    signed_data = self.sign_sign(
                        qube=sign_qube,
                        key_name=sign_key_name,
                        file=path,
                    )

                    io = dvm.run_service_for_stdio(
                        service="qubes.WinSign.Timestamp",
                        input=signed_data,
                    )

                    # TODO: should we keep unsigned binaries?
                    signed_path = str(path) + ".signed"
                    with open(signed_path, "wb") as f:
                        f.write(io[0])

                    os.replace(signed_path, path)

                provision_local_repository(
                    log=self.log,
                    repository_dir=repository_dir,
                    component=self.component,
                    dist=self.dist,
                    target=target,
                    artifacts=artifacts,
                    build_artifacts_dir=artifacts_dir,
                    test_sign=test_sign,
                )

                info = source_info
                info.update(
                    {
                        "artifacts": artifacts.artifacts,
                        "source-hash": self.component.get_source_hash(),
                    }
                )
                self.save_dist_artifacts_info(stage=stage, basename=self.component.name, info=info)
        finally:
            if test_sign:
                self.sign_delete_key(
                    qube=sign_qube,
                    key_name=sign_key_name,
                )

            dvm.kill()

PLUGINS = [WindowsBuildPlugin]
