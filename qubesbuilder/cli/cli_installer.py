from typing import List

import click

from qubesbuilder.cli.cli_base import aliased_group, ContextObj
from qubesbuilder.cli.cli_exc import CliError
from qubesbuilder.common import STAGES, STAGES_ALIAS
from qubesbuilder.config import Config
from qubesbuilder.plugins.installer import InstallerPlugin
from qubesbuilder.template import QubesTemplate


@aliased_group("installer", chain=True)
def installer():
    """
    Installer CLI
    """


def _installer_stage(
    config: Config,
    stage_name: str,
    iso_timestamp: str = None,
    templates: List[QubesTemplate] = None,
    templates_only: bool = False,
):
    """
    Generic function to trigger stage for a template component
    """
    click.echo(f"Running installer stage: {stage_name}")

    ctx = click.get_current_context()
    root_group = ctx.find_root().command

    host_distributions = [
        d for d in config.get_distributions() if d.package_set == "host"
    ]

    if not len(host_distributions) == 1:
        raise CliError("One and only one host distribution must be provided.")

    dist = host_distributions[0]
    installer_plugin = InstallerPlugin(
        dist=dist, config=config, stage=stage_name, templates=templates or []
    )
    if hasattr(installer_plugin, "executor") and hasattr(
        installer_plugin.executor, "cleanup"
    ):
        root_group.add_cleanup(installer_plugin.executor.cleanup)
    installer_plugin.run(
        iso_timestamp=iso_timestamp,
        cache_templates_only=templates_only,
    )


@click.command(name="all", short_help="Run all template stages.")
@click.pass_obj
def _all_installer_stage(obj: ContextObj):
    for s in STAGES:
        _installer_stage(
            config=obj.config,
            templates=obj.templates,
            stage_name=s,
        )


@installer.command()
@click.pass_obj
def fetch(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="fetch",
    )


@installer.command()
@click.option(
    "--iso-timestamp",
    default=None,
    help="Provide ISO timestamp to use (format must be '%Y%m%d%H%M').",
)
@click.pass_obj
def prep(obj: ContextObj, iso_timestamp: str):
    _installer_stage(
        config=obj.config,
        stage_name="prep",
        iso_timestamp=iso_timestamp,
        templates=obj.templates,
    )


@installer.command()
@click.pass_obj
def build(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="build",
    )


@installer.command()
@click.pass_obj
def post(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="post",
    )


@installer.command()
@click.pass_obj
def verify(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="verify",
    )


@installer.command()
@click.pass_obj
def sign(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="sign",
    )


@installer.command()
@click.pass_obj
def publish(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="publish",
    )


@installer.command()
@click.pass_obj
def upload(obj: ContextObj):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="upload",
    )


@installer.command()
@click.option(
    "--templates-only",
    default=False,
    is_flag=True,
    help="Create cache for templates only and skip mock.",
)
@click.pass_obj
def init_cache(obj: ContextObj, templates_only: bool):
    _installer_stage(
        config=obj.config,
        templates=obj.templates,
        stage_name="init-cache",
        templates_only=templates_only,
    )


installer.add_command(init_cache, name="init-cache")
installer.add_command(fetch)
installer.add_command(prep)
installer.add_command(build)
installer.add_command(post)
installer.add_command(verify)
installer.add_command(sign)
installer.add_command(publish)
installer.add_command(upload)
installer.add_command(_all_installer_stage)

installer.add_alias(**STAGES_ALIAS)
