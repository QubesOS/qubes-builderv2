from typing import List, Optional

import click

from qubesbuilder.cli.cli_base import aliased_group, ContextObj
from qubesbuilder.common import STAGES, STAGES_ALIAS
from qubesbuilder.config import Config
from qubesbuilder.template import QubesTemplate


@aliased_group("template", chain=True)
def template():
    """
    Template CLI
    """


def _template_stage(
    config: Config,
    templates: List[QubesTemplate],
    stages: List[str],
    template_timestamp: Optional[str] = None,
):
    """
    Generic function to trigger stage for a template component
    """
    click.echo(f"Running template stages: {', '.join(stages)}")

    ctx = click.get_current_context()
    root_group = ctx.find_root().command

    # Qubes templates
    jobs = config.get_jobs(
        templates=templates, components=[], distributions=[], stages=stages
    )
    for job in jobs:
        if hasattr(job, "executor") and hasattr(job.executor, "cleanup"):
            root_group.add_cleanup(job.executor.cleanup)
        job.run(template_timestamp=template_timestamp)


@click.command(name="all", short_help="Run all template stages.")
@click.pass_obj
def _all_template_stage(obj: ContextObj):
    stages = STAGES
    if obj.config.automatic_upload_on_publish:
        stages.remove("upload")
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=stages,
    )


@template.command()
@click.pass_obj
def fetch(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["fetch"],
    )


@template.command()
@click.option(
    "--template-timestamp",
    default=None,
    help="Provide template timestamp to use (format must be '%Y%m%d%H%M').",
)
@click.pass_obj
def prep(obj: ContextObj, template_timestamp: str):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["prep"],
        template_timestamp=template_timestamp,
    )


@template.command()
@click.pass_obj
def build(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["build"],
    )


@template.command()
@click.pass_obj
def post(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["post"],
    )


@template.command()
@click.pass_obj
def verify(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["verify"],
    )


@template.command()
@click.pass_obj
def sign(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["sign"],
    )


@template.command()
@click.pass_obj
def publish(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["publish"],
    )
    if obj.config.automatic_upload_on_publish:
        _template_stage(
            config=obj.config,
            templates=obj.templates,
            stages=["upload"],
        )


@template.command()
@click.pass_obj
def upload(obj: ContextObj):
    _template_stage(
        config=obj.config,
        templates=obj.templates,
        stages=["upload"],
    )


template.add_command(fetch)
template.add_command(prep)
template.add_command(build)
template.add_command(post)
template.add_command(verify)
template.add_command(sign)
template.add_command(publish)
template.add_command(upload)
template.add_command(_all_template_stage)

template.add_alias(**STAGES_ALIAS)
