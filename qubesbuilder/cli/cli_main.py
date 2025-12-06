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
QubesBuilder command-line interface.
"""
import re
from typing import List, Dict, Any

import click

from qubesbuilder.cli.cli_base import ContextObj, aliased_group
from qubesbuilder.cli.cli_cleanup import cleanup
from qubesbuilder.cli.cli_config import config
from qubesbuilder.cli.cli_exc import CliError
from qubesbuilder.cli.cli_installer import installer
from qubesbuilder.cli.cli_package import package
from qubesbuilder.cli.cli_repository import repository
from qubesbuilder.cli.cli_template import template
from qubesbuilder.common import STAGES, str_to_bool
from qubesbuilder.config import Config, deep_merge
from qubesbuilder.log import init_logger

ALLOWED_KEY_PATTERN = r"[A-Za-z0-9_+-]+"


# Function to validate allowed key values in a dict
def validate_identifier(identifier):
    if re.match(ALLOWED_KEY_PATTERN, identifier) and not any(
        [
            identifier in ("-", "_"),
            identifier.startswith("-"),
            identifier.endswith("-"),
            identifier.startswith("_"),
            identifier.endswith("_"),
        ]
    ):
        return

    raise ValueError(f"Invalid key identifier found: '{identifier}'.")


def parse_dict_from_cli(s, value=None, append=False):
    index_dict = None
    index_array = None

    if value is None:
        # First, consider everything after "=" as value
        if "=" in s:
            s, value = s.split("=", 1)
        # If not, check if we append value
        elif "+" in s:
            s, value = s.split("+", 1)
            append = True

    # Determine if split identifier "+" or ":" is present
    if ":" in s:
        index_dict = s.index(":")
    if "+" in s:
        index_array = s.index("+")

    # If both are present, find the first one and split according to the first one
    if index_dict and index_array:
        split_identifier = ":" if index_dict < index_array else "+"
    elif index_dict:
        split_identifier = ":"
    elif index_array:
        split_identifier = "+"
    else:
        split_identifier = None

    # If no split identifier is found, there is nothing more to parse
    if split_identifier:
        parsed_identifier, remaining_content = s.split(split_identifier, 1)
    else:
        remaining_content = None
        parsed_identifier = s

    if remaining_content:
        # Validate key
        validate_identifier(parsed_identifier)

        if split_identifier == ":":
            if value is None:
                raise ValueError(
                    f"Cannot find '=' or '+' in '{remaining_content}'"
                )
            result = {
                parsed_identifier: parse_dict_from_cli(
                    remaining_content, value=value, append=append
                )
            }
        else:
            result = {
                parsed_identifier: [
                    parse_dict_from_cli(
                        remaining_content, value=value, append=append
                    )
                ]
            }
    else:
        if value is None:
            result = s
        else:
            key = s

            # Validate key
            validate_identifier(key)

            if value.lower() in ("true", "false", "1", "0"):
                value = str_to_bool(value)

            if append:
                value = [value]

            result = {key: value}
    return result


def parse_config_from_cli(array):
    result: Dict[str, Any] = {}
    for s in array:
        # We may have '+components', '+plugins', etc. which are handled
        # only on top level.
        if s.startswith("+"):
            parsed_dict = parse_dict_from_cli(s[1:])
            key = next(iter(parsed_dict.keys()))
            value = parsed_dict[key]
            parsed_dict = {"+" + key: value}
        else:
            parsed_dict = parse_dict_from_cli(s)
        result = deep_merge(result, parsed_dict, allow_append=True)
    return result


def init_context_obj(builder_conf: str, option: List = None):
    try:
        options = parse_config_from_cli(option) if option else {}
    except ValueError as e:
        raise CliError(f"Failed to parse CLI options: '{str(e)}'")
    obj = ContextObj(Config(conf_file=builder_conf, options=options))
    return obj


@aliased_group("qb")
@click.option(
    "--verbose/--no-verbose",
    default=None,
    is_flag=True,
    help="Increase log verbosity.",
)
@click.option(
    "--debug/--no-debug",
    default=None,
    is_flag=True,
    help="Print full traceback on exception.",
)
@click.option(
    "--builder-conf",
    default="builder.yml",
    help="Path to configuration file (default: builder.yml).",
)
@click.option(
    "--log-file",
    default=None,
    help="Path to log file to be created.",
)
@click.option(
    "--component",
    "-c",
    default=None,
    multiple=True,
    help="Specify component to treat (can be repeated).",
)
@click.option(
    "--distribution",
    "-d",
    default=None,
    multiple=True,
    help="Set distribution to treat (can be repeated).",
)
@click.option(
    "--template",
    "-t",
    default=None,
    multiple=True,
    help="Set template to treat (can be repeated).",
)
@click.option(
    "--option",
    "-o",
    default=None,
    multiple=True,
    help="Set builder configuration value (can be repeated).",
)
@click.pass_context
def main(
    ctx: click.Context,
    verbose: bool,
    debug: bool,
    builder_conf: str,
    log_file: str,
    component: List,
    distribution: List,
    template: List,
    option: List,
):
    """
    Main CLI

    """
    obj = init_context_obj(builder_conf=builder_conf, option=option)

    # verbose/debug modes are also provided by builder configuration
    obj.config.set(
        "verbose", verbose if verbose is not None else obj.config.verbose
    )
    obj.config.set("debug", debug if debug is not None else obj.config.debug)

    obj.components = obj.config.get_components(component)
    obj.distributions = obj.config.get_distributions(distribution)
    obj.templates = obj.config.get_templates(template)

    # debug will show traceback
    ctx.command.debug = obj.config.debug

    ctx.obj = obj

    # Define a config session based on click context object
    obj.config.set("session", obj)

    # init QubesBuilderLogger
    init_logger(verbose=obj.config.verbose, log_file=log_file)


main.epilog = f"""Stages:
    {' '.join(STAGES)}

Option:
    Input value for option is of the form:

        1. key=value
        2. parent-key:key=value
        3. key+value

    It allows to set configuration dict values or appending array values.
    In the three forms, 'value' can be chained by one of the three forms to set value at deeper level.

    For example:
        force-fetch=true
        executor:type=qubes
        executor:options:dispvm=builder-dvm
        components+lvm2
        components+kernel:branch=stable-5.15
        cache:templates+debian-12

Remark:
    The Qubes OS components are separated into two groups: standard components
    and template components. Standard components will produce distribution
    packages to be installed in TemplateVMs or StandaloneVMs, while template
    components will produce template packages to be installed via qvm-template.
"""

main.add_command(package)
main.add_command(template)
main.add_command(repository)
main.add_command(installer)
main.add_command(config)
main.add_command(cleanup)
