import subprocess
from typing import List

import click

from qubesbuilder.cli.cli_base import (
    aliased_group,
    AliasedGroup,
    ContextObj,
    print_pipeline,
)
from qubesbuilder.common import STAGES, STAGES_ALIAS
from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.log import QubesBuilderLogger


@aliased_group("package", chain=True)
def package():
    """
    Package CLI
    """


def _component_stage(
    config: Config,
    components: List[QubesComponent],
    distributions: List[QubesDistribution],
    stages: List[str],
    **kwargs,
):
    """
    Generic function to trigger stage for a standard component
    """
    QubesBuilderLogger.info(f"Running stages: {', '.join(stages)}")

    root_group: AliasedGroup | None = None
    try:
        ctx = click.get_current_context()
        cmd = ctx.find_root().command
        if isinstance(cmd, AliasedGroup):
            root_group = cmd
    except RuntimeError:
        pass

    if config.get("skip-git-fetch", "default") == "default":
        config.set("skip-git-fetch", "fetch" not in stages)
    # Only upload and init-cache skip fetch; all other stages need it.
    stages_needing_fetch = {
        "fetch",
        "prep",
        "build",
        "post",
        "verify",
        "sign",
        "publish",
    }

    fetch_done_set: set = config.get("session-fetch-done", set())
    component_keys = {c.name for c in components}
    if not component_keys.issubset(fetch_done_set) and any(
        s in stages_needing_fetch for s in stages
    ):
        for job in config.get_jobs(
            components=components,
            distributions=distributions,
            templates=[],
            stages=["fetch"],
        ):
            if (
                hasattr(job, "executor")
                and hasattr(job.executor, "cleanup")
                and root_group
            ):
                root_group.add_cleanup(job.executor.cleanup)
            job.run(**kwargs)
        fetch_done_set.update(component_keys)
        config.set("session-fetch-done", fetch_done_set)

    if "fetch" in stages:
        stages.remove("fetch")

    # Track jobs already run this session to avoid running dep jobs twice
    # when stages are chained (e.g. "package prep build").
    # The key includes dist and component so jobs for different dists are not skipped.
    session_jobs_done: set = config.get("session-jobs-done", set())

    for job in config.get_jobs(
        components=components,
        distributions=distributions,
        templates=[],
        stages=stages,
        with_dependencies=True,
    ):
        # fetch was already run above.
        if job.stage == "fetch":
            continue
        job_key = (
            job.stage,
            getattr(job, "component", None),
            getattr(job, "dist", None),
        )
        # Skip jobs already run earlier in this session.
        if job_key in session_jobs_done:
            continue
        if (
            hasattr(job, "executor")
            and hasattr(job.executor, "cleanup")
            and root_group
        ):
            root_group.add_cleanup(job.executor.cleanup)
        job.run(**kwargs)
        session_jobs_done.add(job_key)

    config.set("session-jobs-done", session_jobs_done)


@click.command(name="all", short_help="Run all package stages.")
@click.pass_obj
def _all_package_stage(obj: ContextObj):
    stages = obj.config.get_stages()
    if obj.config.automatic_upload_on_publish:
        stages.remove("upload")
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=stages,
    )


@package.command()
@click.pass_obj
def fetch(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["fetch"],
    )


@package.command()
@click.pass_obj
def prep(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["prep"],
    )


@package.command()
@click.pass_obj
def build(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["build"],
    )


@package.command()
@click.pass_obj
def post(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["post"],
    )


@package.command()
@click.pass_obj
def verify(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["verify"],
    )


@package.command()
@click.pass_obj
def sign(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["sign"],
    )


@package.command()
@click.pass_obj
def publish(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["publish"],
    )
    if obj.config.automatic_upload_on_publish:
        _component_stage(
            config=obj.config,
            components=obj.components,
            distributions=obj.distributions,
            stages=["upload"],
        )


@package.command()
@click.pass_obj
def upload(obj: ContextObj):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["upload"],
    )


@package.command()
@click.option(
    "--force",
    default=False,
    is_flag=True,
    help="Force cleanup and recreation.",
)
@click.pass_obj
def init_cache(obj: ContextObj, force: bool = False):
    _component_stage(
        config=obj.config,
        components=obj.components,
        distributions=obj.distributions,
        stages=["init-cache"],
        force=force,
    )


@package.command()
@click.pass_obj
def diff(obj: ContextObj):
    for component in obj.components:
        if not component.source_dir.exists():
            continue
        status = subprocess.run(
            f"git -C {component.source_dir} status | grep '^nothing to commit'",
            shell=True,
            stdout=subprocess.DEVNULL,
        )
        if not status.returncode == 0:
            subprocess.run(
                f"(echo -e 'Uncommitted changes in {component.source_dir}:\n\n'; git -C {component.source_dir} diff --color=always) | less -RM +Gg",
                shell=True,
            )


@package.command()
@click.argument("stages", nargs=-1)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "yaml"], case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format: human-readable text or YAML.",
)
@click.option(
    "--no-deps",
    is_flag=True,
    default=False,
    help="Show only the requested stages without resolving dependencies.",
)
@click.pass_obj
def pipeline(obj: ContextObj, stages, fmt, no_deps):
    """
    Show the ordered pipeline of jobs for the given STAGES (default: all).

    By default dependencies are resolved so the output matches the actual
    execution order when that stage is invoked from the CLI.
    """
    if not stages:
        stages = list(STAGES)
    jobs = obj.config.get_jobs(
        components=obj.components,
        distributions=obj.distributions,
        templates=[],
        stages=list(stages),
        with_dependencies=not no_deps,
    )
    print_pipeline(jobs, fmt=fmt)


# stages commands
package.add_command(init_cache, name="init-cache")
package.add_command(prep)
package.add_command(build)
package.add_command(post)
package.add_command(verify)
package.add_command(sign)
package.add_command(publish)
package.add_command(upload)
package.add_command(_all_package_stage)

# utils commands
package.add_command(diff)
package.add_command(pipeline)

package.add_alias(**STAGES_ALIAS)
