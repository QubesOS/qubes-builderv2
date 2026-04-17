from typing import List, Optional

import click

from qubesbuilder.cli.cli_base import (
    aliased_group,
    AliasedGroup,
    ContextObj,
    print_pipeline,
)
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

    # Mark whether 'prep' was explicitly requested on the CLI.
    # This lets the TemplateBuilderPlugin distinguish "explicit prep"
    # from "prep only because of a dependency (e.g. build)".
    config.set("force-template-prep", "prep" in stages)

    root_group: AliasedGroup | None = None
    try:
        ctx = click.get_current_context()
        cmd = ctx.find_root().command
        if isinstance(cmd, AliasedGroup):
            root_group = cmd
    except RuntimeError:
        pass

    # Qubes templates
    jobs = config.get_jobs(
        templates=templates, components=[], distributions=[], stages=stages
    )
    for job in jobs:
        if (
            hasattr(job, "executor")
            and hasattr(job.executor, "cleanup")
            and root_group
        ):
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


@template.command()
@click.argument("stages", nargs=-1)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "yaml"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format: human-readable text or YAML.",
)
@click.pass_obj
def pipeline(obj: ContextObj, stages, fmt):
    """Show the ordered pipeline of jobs for the given STAGES (default: all)."""
    if not stages:
        stages = list(STAGES)
    pl = obj.config.get_pipeline(
        components=[],
        distributions=[],
        templates=obj.templates,
        stages=list(stages),
    )
    jobs = pl.sorted_jobs(obj.config)
    print_pipeline(jobs, fmt=fmt)


template.add_command(fetch)
template.add_command(prep)
template.add_command(build)
template.add_command(post)
template.add_command(verify)
template.add_command(sign)
template.add_command(publish)
template.add_command(upload)
template.add_command(_all_template_stage)
template.add_command(pipeline)

template.add_alias(**STAGES_ALIAS)
