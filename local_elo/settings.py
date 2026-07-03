"""CLI argument definitions and stored-settings (JSON) support.

This module is the single source of truth for the command-line parameters. The
same ``argparse`` parser drives three things so they can never drift apart:

* parsing the actual command line,
* reading stored settings from a JSON file (values are funnelled through the
  very same parser via a synthesized argv), and
* writing the current settings back out (the ``store`` command walks the same
  parser's actions to decide what to serialize).

Precedence when resolving a run's settings: built-in defaults < stored JSON <
command-line flags.
"""

import argparse
import json
import os
import sys

from .constants import CONFIG_NAME, DEFAULT_GAMES_POWER, DEFAULT_ELO_POWER
from .colors import red, yellow

# Parameters that are never read from / written to the config file:
#   target_dir - locates the config itself (self-referential)
#   refresh    - a one-shot action, not a persistent setting
NON_CONFIG_DESTS = {'target_dir', 'refresh'}


def parse_pool_size(value: str):
    """Parse pool size argument in X/Y format.

    X = total pool size
    Y = number selected via top-skewing weighted (remaining X-Y use custom weighted)

    Examples:
      200/50 - Total pool of 200: 150 custom weighted + 50 top-skewing weighted
      32     - Total pool of 32: all custom weighted (equivalent to 32/0)
    """
    from .knockout import PoolConfig

    try:
        if '/' in value:
            parts = value.split('/')
            if len(parts) != 2:
                raise argparse.ArgumentTypeError(
                    f"Invalid pool size format '{value}'. Expected 'X/Y' format"
                )

            if not parts[0] or not parts[1]:
                raise argparse.ArgumentTypeError(
                    f"Invalid pool size format '{value}'. Both X and Y must be specified"
                )

            total_size = int(parts[0])
            top_skewing_size = int(parts[1])

            # Validate constraints
            if total_size < 2:
                raise argparse.ArgumentTypeError(
                    "Total pool size (X) must be at least 2"
                )
            if top_skewing_size < 0:
                raise argparse.ArgumentTypeError(
                    "Top-skewing size (Y) cannot be negative"
                )
            if top_skewing_size > total_size:
                raise argparse.ArgumentTypeError(
                    f"Top-skewing size (Y={top_skewing_size}) cannot exceed total size (X={total_size})"
                )

            return PoolConfig(total_size=total_size, top_skewing_size=top_skewing_size)
        else:
            # Single number format: X defaults to all custom weighted (Y=0)
            total_size = int(value)
            if total_size < 2:
                raise argparse.ArgumentTypeError("Pool size must be at least 2")
            return PoolConfig(total_size=total_size, top_skewing_size=0)

    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid pool size '{value}'. Must be integer or 'X/Y' format"
        )


def parse_power(value: str):
    """Parse power parameter in X/Y format.

    X = games power, Y = elo power
    Examples:
        '10/5' -> (10.0, 5.0)
        '10/' -> (10.0, DEFAULT_ELO_POWER)
        '/5' -> (DEFAULT_GAMES_POWER, 5.0)
        '10' -> (10.0, DEFAULT_ELO_POWER)
    """
    try:
        if '/' in value:
            parts = value.split('/')
            if len(parts) != 2:
                raise argparse.ArgumentTypeError(
                    f"Invalid power format '{value}'. Expected 'X/Y' format"
                )
            games_power = float(parts[0]) if parts[0] else DEFAULT_GAMES_POWER
            elo_power = float(parts[1]) if parts[1] else DEFAULT_ELO_POWER
        else:
            games_power = float(value)
            elo_power = DEFAULT_ELO_POWER

        return (games_power, elo_power)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid power '{value}'. Must be float or 'X/Y' format"
        )


def format_power(value) -> str:
    """Inverse of parse_power: (games, elo) tuple -> 'games/elo' string."""
    games_power, elo_power = value
    return f"{games_power}/{elo_power}"


def format_pool_size(value) -> str:
    """Inverse of parse_pool_size: PoolConfig -> 'X/Y' string."""
    return f"{value.total_size}/{value.top_skewing_size}"


# Per-dest serializers used when writing the config file. Any eligible dest not
# listed here is stored as its native JSON value (str/int/bool/None).
SERIALIZERS = {
    'power': format_power,
    'pool_size': format_pool_size,
}


def build_parser() -> argparse.ArgumentParser:
    """Build the Local Elo argument parser (the single CLI definition)."""
    parser = argparse.ArgumentParser(description='Local Elo - Rank files using Elo ratings')
    parser.add_argument('target_dir', nargs='?', default='.',
                       help='Target directory to search for files (default: current directory)')
    parser.add_argument('-e', '--extension', dest='extensions', default=None,
                       help='File extensions to include (comma-separated, e.g., "py,js,ts")')
    parser.add_argument('-k', '--knockout', action='store_true',
                       help='Knockout mode: eliminate losers until only one remains')
    parser.add_argument('-p', '--power', dest='power', type=parse_power, default=(2.0, 2.0),
                       help='Power factors in X/Y format: X=games power, Y=elo power (default: 2.0/2.0). '
                            'Examples: -p 10/5 (games=10, elo=5), -p 10/ (games=10, elo=default), -p /5 (games=default, elo=5)')
    parser.add_argument('-n', '--pool-size', dest='pool_size', type=parse_pool_size, default=None,
                       help='Tournament pool selection in X/Y format. '
                            'X = total pool size, Y = top-skewing weighted (remaining X-Y custom weighted). '
                            'Custom weighted uses power param, top-skewing uses hardcoded constant. '
                            'Examples: -n 200/50 (200 total: 150 custom + 50 top-skewing), '
                            '-n 32 (32 total: all custom weighted). '
                            '(default: use all remaining files)')
    parser.add_argument('-l', '--link', dest='link_pattern', default=None,
                       help='URL pattern for clickable links (use * as placeholder for filename, e.g., "linkedin.com/in/*")')
    parser.add_argument('-m', '--match-size', dest='match_size', type=int, default=2,
                       help='Number of players shown in each competition round (default: 2, max: 26)')
    parser.add_argument('-r', '--refresh', action='store_true',
                       help='Delete DB entries whose files no longer exist on disk, '
                            'recalculate remaining Elos (total = N x 1000), then exit.')
    return parser


def config_path(target_dir: str) -> str:
    """Path to the settings JSON file for a target directory."""
    return os.path.join(target_dir, CONFIG_NAME)


def load_config(target_dir: str) -> dict:
    """Load stored settings from the target dir.

    Returns {} if no config file exists. A present-but-malformed file (invalid
    JSON or not a JSON object) is a user mistake, so we fail loud and exit.
    """
    path = config_path(target_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(red(f"Error reading settings file {path}: {exc}"))
        sys.exit(1)
    if not isinstance(data, dict):
        print(red(f"Error: settings file {path} must contain a JSON object"))
        sys.exit(1)
    return data


def _is_flag(action) -> bool:
    """True if an argparse action takes no value (e.g. store_true)."""
    return action.nargs == 0


def config_actions(parser: argparse.ArgumentParser):
    """Yield the parser actions that correspond to a persistable setting.

    This is the single definition of "which params live in the config file",
    reused by every read/write path so they stay in sync. It excludes the
    positional target_dir, the auto-added --help action, and NON_CONFIG_DESTS.
    """
    for action in parser._actions:
        if not action.option_strings:            # positional (target_dir)
            continue
        if isinstance(action, argparse._HelpAction):
            continue
        if action.dest in NON_CONFIG_DESTS:
            continue
        yield action


def config_to_argv(config: dict, parser: argparse.ArgumentParser) -> list:
    """Turn a stored-settings dict into a synthetic argv for `parser`.

    This is what keeps reading DRY: values funnel through the exact same
    converters and validation as the real command line. Unknown or ineligible
    keys are warned about and skipped; keys set to null are treated as unset.
    """
    by_dest = {a.dest: a for a in config_actions(parser)}
    argv = []
    for key, value in config.items():
        action = by_dest.get(key)
        if action is None:
            print(yellow(f"Warning: ignoring unknown setting '{key}' in {CONFIG_NAME}"))
            continue
        if value is None:
            continue
        opt = action.option_strings[0]
        if _is_flag(action):
            if value:
                argv.append(opt)
        else:
            argv.extend([opt, str(value)])
    return argv


def resolve_settings(argv=None) -> argparse.Namespace:
    """Resolve effective settings: defaults < stored JSON < command-line flags."""
    parser = build_parser()

    # Layer 1: built-in defaults.
    defaults = vars(build_parser().parse_args([]))

    # target_dir is needed to locate the config file; take it from the CLI.
    cli_args = parser.parse_args(argv)

    # Layer 2: stored settings, funnelled through the same parser.
    config = load_config(cli_args.target_dir)
    stored_argv = config_to_argv(config, parser)
    try:
        stored_ns = build_parser().parse_args(stored_argv)
    except SystemExit:
        # argparse already printed a usage error for the bad value; make the
        # source unambiguous before exiting.
        print(red(f"Error: invalid value in {CONFIG_NAME}"))
        raise
    stored = {a.dest: getattr(stored_ns, a.dest) for a in config_actions(parser)
              if a.dest in config}

    # Layer 3: only the flags actually passed on the command line. Using
    # SUPPRESS as the default means unspecified options don't appear at all.
    sup = build_parser()
    for action in sup._actions:
        action.default = argparse.SUPPRESS
    provided = vars(sup.parse_args(argv))

    return argparse.Namespace(**{**defaults, **stored, **provided})


def settings_to_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict:
    """Serialize the current effective settings into a JSON-ready dict.

    Includes every config-eligible parameter (a full snapshot), applying the
    per-dest serializer when the runtime value isn't natively JSON-friendly.
    """
    config = {}
    for action in config_actions(parser):
        dest = action.dest
        value = getattr(args, dest, None)
        serializer = SERIALIZERS.get(dest)
        config[dest] = serializer(value) if (serializer and value is not None) else value
    return config


def save_config(target_dir: str, config: dict) -> str:
    """Write settings to the target dir's JSON file. Returns the path written."""
    path = config_path(target_dir)
    with open(path, 'w') as f:
        json.dump(config, f, indent=2, sort_keys=True)
        f.write('\n')
    return path
