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

import click

from qubesbuilder.cli.cli_base import aliased_group, ContextObj
from qubesbuilder.cli.cli_exc import CliError
from qubesbuilder.self_upgrade import (
    SelfUpgradeError,
    run_self_check,
    run_self_upgrade,
)


@aliased_group("self")
def self_group():
    """
    Self-management CLI (upgrade qubes-builderv2 in place).

    This group is intentionally not chained: 'qb self upgrade' must be run on
    its own. Combining it with other subcommands would mix old in-memory
    Python modules with newly fetched on-disk plugin scripts.
    """


@self_group.command(name="upgrade")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be done without modifying the checkout.",
)
@click.pass_obj
def upgrade(obj: ContextObj, dry_run: bool):
    """
    Upgrade qubes-builderv2 in place via git fetch + signature verification.

    The artifacts directory is never touched. The upgrade is fast-forward only.
    Local commits and uncommitted changes block the upgrade.
    """
    try:
        run_self_upgrade(obj.config, dry_run=dry_run)
    except SelfUpgradeError as e:
        raise CliError(str(e))


@self_group.command(name="check")
@click.pass_obj
def check(obj: ContextObj):
    """
    Check whether a newer qubes-builderv2 is available, without touching the
    checkout.

    Uses 'git ls-remote' and reports whether the tip is already in local
    history. Ignores the daily throttle. Signatures are checked only during
    'qb self upgrade'.
    """
    try:
        run_self_check(obj.config)
    except SelfUpgradeError as e:
        raise CliError(str(e))
