# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2022 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
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

"""
QubesBuilder command-line interface - base module.
"""
import asyncio
import signal
import sys
import traceback
from typing import Callable, List

import click

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.log import QubesBuilderLogger
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.template import QubesTemplate


class ContextObj:
    """
    Helper object for keeping state in :attr:`click.Context.obj`
    """

    def __init__(self, config: Config):
        self.config = config
        self.manager = PluginManager(config.get_plugins_dirs())
        self.components: List[QubesComponent] = []
        self.distributions: List[QubesDistribution] = []
        self.templates: List[QubesTemplate] = []
        self.dry_run = False


class AliasedGroup(click.Group):
    """
    A very simple alias engine for :class:`click.Group`
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aliases = {}
        self.debug = False
        self.list_commands = self.list_commands_for_help  # type: ignore

    def __call__(self, *args, **kwargs):
        loop = asyncio.get_event_loop()

        def _handle_interrupt():
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()

        loop.add_signal_handler(signal.SIGINT, _handle_interrupt)

        rc = 1
        try:
            rv = self.main(*args, standalone_mode=False, **kwargs)
            if isinstance(rv, list) and set(rv) == {None}:
                rc = 0
        except Exception as exc:
            if isinstance(exc, click.Abort) or "Signals.SIGINT" in str(exc):
                QubesBuilderLogger.warning(f"Interrupting...")
            else:
                QubesBuilderLogger.error(f"An error occurred: {str(exc)}")
                if self.debug is True:
                    formatted_traceback = "".join(
                        traceback.format_exception(exc)
                    )
                    QubesBuilderLogger.error(
                        "\n" + formatted_traceback.rstrip("\n")
                    )
                if (
                    hasattr(exc, "additional_info")
                    and isinstance(exc.additional_info, dict)
                    and exc.additional_info.get("log_file", None)
                    and exc.additional_info.get("start_line", None)
                    and exc.additional_info.get("lines", None)
                ):
                    QubesBuilderLogger.error(
                        f"Additional information from {exc.additional_info['log_file']} line {exc.additional_info['start_line']}:"
                    )
                    for line in exc.additional_info["lines"]:
                        QubesBuilderLogger.error(f">>> {line}")
                if isinstance(exc, click.ClickException):
                    # pylint: disable=no-member
                    rc = exc.exit_code
        finally:
            sys.exit(rc)

    def get_command(self, ctx, cmd_name):
        rv = click.Group.get_command(self, ctx, cmd_name)
        if rv is not None:
            return rv
        matches = [x for x in self.list_commands(ctx) if x.startswith(cmd_name)]
        if not matches:
            return None
        elif len(matches) == 1:
            return click.Group.get_command(self, ctx, matches[0])
        ctx.fail(f"Too many matches: {', '.join(sorted(matches))}")

    def resolve_command(self, ctx, args):
        _, cmd, args = super().resolve_command(ctx, args)
        return cmd.name if cmd else None, cmd, args

    def add_alias(self, **kwargs):
        """
        Add aliases to a command

        >>> cmd.add_alias(alias='original-command')
        """
        assert all(
            alias not in (*self.aliases, *self.commands) for alias in kwargs
        )
        self.aliases.update(kwargs)

    def format_epilog(self, ctx, formatter):
        if self.epilog:
            formatter.write_paragraph()
            for line in self.epilog.split("\n"):
                formatter.write_text(line)

    def list_commands_for_help(self, ctx):
        return list(self.commands.keys())


def aliased_group(name=None, **kwargs) -> Callable[[Callable], AliasedGroup]:
    """
    A decorator that creates an AliasedGroup.
    """

    def decorator(f):
        return click.group(name, cls=AliasedGroup, **kwargs)(f)

    return decorator
