import os
import re
import shutil
from datetime import datetime, timedelta

import click

from qubesbuilder.cli.cli_base import aliased_group, ContextObj
from qubesbuilder.common import get_archive_name
from qubesbuilder.component import QubesComponent, ComponentError, QubesVersion

TEMPLATE_RPM_RE = re.compile(
    r"^(?P<name>qubes-template-.+?)-"
    r"(?P<version>[0-9][^-]*)-(?P<release>[^-]+)\.noarch\.rpm$"
)


@aliased_group("cleanup", chain=True)
@click.option(
    "--dry-run",
    default=False,
    is_flag=True,
    help="Dry run. It won't delete anything.",
)
@click.pass_obj
def cleanup(obj: ContextObj, dry_run: bool):
    """
    Cleanup CLI
    """
    obj.dry_run = dry_run


def get_component_distfiles(component: QubesComponent):
    current_distfiles = []
    try:
        for file in (
            component.get_parameters().get("source", {}).get("files", [])
        ):
            distfile_fname = get_archive_name(file)
            if distfile_fname:
                current_distfiles.append(distfile_fname)

            if file.get("signature", None):
                current_distfiles.append(os.path.basename(file["signature"]))

    except ComponentError:
        pass

    return current_distfiles


@click.command()
@click.pass_obj
def distfiles(obj: ContextObj):
    """
    Cleanup previous distribution files versions.
    """
    for component in obj.components:
        current_distfiles = get_component_distfiles(component)

        if not (obj.config.distfiles_dir / component.name).exists():
            continue

        for file in (obj.config.distfiles_dir / component.name).iterdir():
            if file.name not in current_distfiles:
                if obj.dry_run:
                    click.secho(f"DRY-RUN: {file}")
                else:
                    click.secho(file)
                    file.unlink()


def _installer_active_chroots(obj: ContextObj) -> set:
    host_dists = [
        d for d in obj.config.get_distributions() if d.package_set == "host"
    ]
    return {d.nva for d in host_dists}


def _installer_active_template_prefixes(obj: ContextObj) -> set:
    try:
        release = obj.config.parse_qubes_release().group(1)
    except Exception:
        return set()
    return {
        f"qubes-template-{t}-{release}.0"
        for t in obj.config.get("cache", {}).get("templates", [])
    }


def _group_template_rpms_by_name(rpms):
    groups: dict = {}
    for rpm in rpms:
        m = TEMPLATE_RPM_RE.match(rpm.name)
        if not m:
            continue
        prefix = f"{m.group('name')}-{m.group('version')}"
        groups.setdefault(prefix, []).append(rpm)
    return groups


@click.command("build-artifacts")
@click.option(
    "--keep-versions", default=3, help="Number of old versions to keep."
)
@click.pass_obj
def build_artifacts(obj: ContextObj, keep_versions):
    """
    Cleanup old build artifacts.
    """
    for component in obj.components:
        component_dir = obj.config.artifacts_dir / "components" / component.name
        if not component_dir.exists() or not component.has_packages:
            continue

        versions = sorted(
            [v.name for v in component_dir.iterdir() if v.name != "noversion"],
            reverse=True,
            key=QubesVersion,
        )
        for version in versions[keep_versions:]:
            version_path = component_dir / version
            if obj.dry_run:
                click.secho(f"DRY-RUN: {version_path}")
            else:
                click.secho(version_path)
                shutil.rmtree(version_path)


@click.command()
@click.option(
    "--log-retention-days", default=30, help="Number of days to keep logs."
)
@click.pass_obj
def logs(obj: ContextObj, log_retention_days):
    """
    Cleanup old logs.
    """
    logs_dir = obj.config.logs_dir
    cutoff_date = datetime.now() - timedelta(days=log_retention_days)

    for log_file in logs_dir.iterdir():
        if (
            log_file.is_file()
            and datetime.fromtimestamp(log_file.stat().st_mtime) < cutoff_date
        ):
            if obj.dry_run:
                click.secho(f"DRY-RUN: {log_file}")
            else:
                click.secho(log_file)
                log_file.unlink()


@click.command()
@click.option(
    "--force",
    default=False,
    is_flag=True,
    help="Force temporary directory cleanup.",
)
@click.pass_obj
def tmp(obj: ContextObj, force: bool):
    """
    Cleanup temporary files and directories.
    """
    if not obj.config.temp_dir.exists():
        return
    for temp_dir in obj.config.temp_dir.iterdir():
        # remove empty directory only unless force
        if force or not list(temp_dir.iterdir()):
            if obj.dry_run:
                click.secho(f"DRY-RUN: {temp_dir}")
            else:
                click.secho(temp_dir)
                shutil.rmtree(temp_dir)


@click.command()
@click.option(
    "--all",
    default=False,
    is_flag=True,
    help="Cleanup all caches including chroot and installer.",
)
@click.option(
    "--chroot/--no-chroot",
    default=False,
    is_flag=True,
    help="Cleanup chroot cache.",
)
@click.option(
    "--chroot-only-unused",
    default=False,
    is_flag=True,
    help="Cleanup unused chroot cache.",
)
@click.option(
    "--installer/--no-installer",
    default=False,
    is_flag=True,
    help="Cleanup installer cache.",
)
@click.option(
    "--installer-chroot/--no-installer-chroot",
    default=False,
    is_flag=True,
    help="Cleanup installer chroot cache.",
)
@click.option(
    "--installer-templates/--no-installer-templates",
    default=False,
    is_flag=True,
    help="Cleanup installer templates cache.",
)
@click.option(
    "--installer-bootstrap/--no-installer-bootstrap",
    default=False,
    is_flag=True,
    help="Cleanup installer bootstrap cache (prep stage cache content).",
)
@click.option(
    "--installer-chroot-only-unused",
    default=False,
    is_flag=True,
    help="Cleanup installer chroot cache entries for dists no longer configured.",
)
@click.option(
    "--installer-templates-only-unused",
    default=False,
    is_flag=True,
    help="Cleanup cached templates no longer listed in cache.templates.",
)
@click.option(
    "--installer-templates-only-old",
    default=False,
    is_flag=True,
    help="Keep only the most recent RPM for each cached template name.",
)
@click.pass_obj
def cache(
    obj: ContextObj,
    all: bool,
    chroot: bool,
    chroot_only_unused: bool,
    installer: bool,
    installer_chroot: bool,
    installer_templates: bool,
    installer_bootstrap: bool,
    installer_chroot_only_unused: bool,
    installer_templates_only_unused: bool,
    installer_templates_only_old: bool,
):
    """
    Cleanup cache files and directories.
    """
    if all:
        chroot = True
        installer = True

    installer_cache = obj.config.cache_dir / "installer"
    installer_chroot_dir = installer_cache / "chroot" / "mock"
    installer_templates_dir = installer_cache / "templates"

    to_delete = []
    if chroot:
        to_delete.append(obj.config.cache_dir / "chroot")
    if chroot_only_unused:
        distributions = [dist.name for dist in obj.distributions]
        for chroot_dir in (obj.config.cache_dir / "chroot").iterdir():
            if chroot_dir.name not in distributions:
                to_delete.append(chroot_dir)
    if installer:
        to_delete.append(installer_cache)
    if installer_chroot:
        to_delete.append(installer_chroot_dir)
    if installer_templates:
        to_delete.append(installer_templates_dir)
    if installer_bootstrap and installer_cache.exists():
        bootstrap_dirs = sorted(
            [
                bootstrap_dir
                for bootstrap_dir in installer_cache.iterdir()
                if bootstrap_dir.name.startswith("Qubes-")
            ],
            reverse=True,
        )
        to_delete += bootstrap_dirs[1:]
    if installer_chroot_only_unused and installer_chroot_dir.exists():
        active = _installer_active_chroots(obj)
        for entry in installer_chroot_dir.iterdir():
            if entry.name not in active:
                to_delete.append(entry)
    if (
        installer_templates_only_unused or installer_templates_only_old
    ) and installer_templates_dir.exists():
        rpms = [
            p
            for p in installer_templates_dir.iterdir()
            if p.is_file() and p.suffix == ".rpm"
        ]
        groups = _group_template_rpms_by_name(rpms)
        active = _installer_active_template_prefixes(obj)
        for name, files in groups.items():
            if installer_templates_only_unused and name not in active:
                to_delete.extend(files)
                continue
            if installer_templates_only_old and name in active:
                files.sort(reverse=True)
                to_delete.extend(files[1:])

    for path in to_delete:
        if not path.exists():
            continue
        if obj.dry_run:
            click.secho(f"DRY-RUN: {path}")
        else:
            click.secho(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


@click.command()
@click.option(
    "--keep-versions", default=3, help="Number of old versions to keep."
)
@click.option(
    "--log-retention-days", default=30, help="Number of days to keep logs."
)
@click.option(
    "--force-tmp",
    default=False,
    is_flag=True,
    help="Force temporary directory cleanup.",
)
@click.option(
    "--all-cache",
    default=False,
    is_flag=True,
    help="Cleanup all caches including chroot and installer.",
)
@click.option(
    "--chroot/--no-chroot",
    default=False,
    is_flag=True,
    help="Cleanup chroot cache.",
)
@click.option(
    "--installer/--no-installer",
    default=False,
    is_flag=True,
    help="Cleanup installer cache.",
)
@click.option(
    "--installer-chroot/--no-installer-chroot",
    default=False,
    is_flag=True,
    help="Cleanup installer chroot cache.",
)
@click.option(
    "--installer-templates/--no-installer-templates",
    default=False,
    is_flag=True,
    help="Cleanup installer templates cache.",
)
@click.option(
    "--installer-bootstrap/--no-installer-bootstrap",
    default=False,
    is_flag=True,
    help="Cleanup installer bootstrap cache (prep stage cache content).",
)
@click.option(
    "--installer-only-outdated/--no-installer-only-outdated",
    default=True,
    is_flag=True,
    help="Prune outdated installer chroot/templates cache entries "
    "(ignored when --all-cache or --everything is set).",
)
@click.option(
    "--everything",
    default=False,
    is_flag=True,
    help="/!\\ Cleanup everything. It turns on every options to force removal all cache directories and files /!\\",
)
@click.pass_context
def all(
    ctx,
    keep_versions,
    log_retention_days,
    force_tmp,
    all_cache,
    chroot,
    installer,
    installer_chroot,
    installer_templates,
    installer_bootstrap,
    installer_only_outdated,
    everything,
):
    """
    Cleanup all.
    """
    if everything:
        keep_versions = 0
        log_retention_days = 0
        force_tmp = True
        all_cache = True

    prune_outdated = installer_only_outdated and not all_cache

    ctx.invoke(distfiles)
    ctx.invoke(build_artifacts, keep_versions=keep_versions)
    ctx.invoke(logs, log_retention_days=log_retention_days)
    ctx.invoke(tmp, force=force_tmp)
    ctx.invoke(
        cache,
        all=all_cache,
        chroot=chroot,
        installer=installer,
        installer_chroot=installer_chroot,
        installer_templates=installer_templates,
        installer_bootstrap=installer_bootstrap or prune_outdated,
        installer_chroot_only_unused=prune_outdated,
        installer_templates_only_unused=prune_outdated,
        installer_templates_only_old=prune_outdated,
    )


cleanup.add_command(distfiles)
cleanup.add_command(build_artifacts)
cleanup.add_command(logs)
cleanup.add_command(tmp)
cleanup.add_command(cache)
cleanup.add_command(all)
