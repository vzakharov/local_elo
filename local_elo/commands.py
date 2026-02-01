import sqlite3
import sys
import os
import argparse

from .db import init_db, get_active_files, get_rankings
from .elo import calculate_win_probability
from .files import handle_open_command, handle_rename_command, handle_rem_command, handle_add_command, sync_files
from .ui import display_leaderboard, format_record, parse_top_command, display_welcome_message, format_matchup
from .game import select_first_player, select_second_player
from .knockout import (
    handle_game_result, handle_reset_command, initialize_knockout_tournament, handle_winner_screen
)
from .colors import red, yellow, dim
from .utils import display_name, extensions_to_pattern


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
    from .constants import DEFAULT_GAMES_POWER, DEFAULT_ELO_POWER

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


def main():
    """Main entry point for the Local Elo CLI tool."""
    # Parse command line arguments
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
    args = parser.parse_args()

    # Set global link pattern
    if args.link_pattern:
        from . import utils
        utils.LINK_PATTERN = args.link_pattern

    # Convert extensions to regex pattern
    if args.extensions:
        pattern = extensions_to_pattern(args.extensions)
    else:
        pattern = '.*'  # Match all files by default

    # Validate power parameters
    games_power, elo_power = args.power
    if games_power <= 0 or elo_power <= 0:
        print(red(f"Error: Power parameters must be positive (got games={games_power}, elo={elo_power})"))
        sys.exit(1)

    # Initialize database
    conn = init_db(args.target_dir)

    try:
        if args.knockout:
            eliminated, tournament_pool = initialize_knockout_tournament(
                conn, args.target_dir, pattern, args.pool_size, args.power
            )
        else:
            eliminated = set()
            tournament_pool = set()

        display_welcome_message(args.knockout)

        while True:
            # Sync files with database
            sync_files(conn, pattern, args.target_dir)

            # Get active files
            files = get_active_files(conn, args.target_dir, pattern)

            # In knockout mode, filter by tournament pool and eliminated players
            if args.knockout:
                if tournament_pool:
                    # Only include files in the tournament pool
                    files = [f for f in files if f[0] in tournament_pool and f[0] not in eliminated]
                else:
                    # No pool restriction, just filter eliminated
                    files = [f for f in files if f[0] not in eliminated]

            if len(files) == 0:
                print(yellow("No files found matching the pattern."))
                break

            if len(files) == 1:
                if args.knockout:
                    should_exit = handle_winner_screen(
                        conn, args.target_dir, pattern, eliminated, tournament_pool
                    )
                    if should_exit:
                        break
                else:
                    print(yellow("Only one file found. Need at least two files for comparison."))
                    break

            # Select two players
            first_player = select_first_player(files, args.power)
            second_player = select_second_player(files, first_player)

            if second_player is None:
                print(red("Could not find a second player."))
                break

            # Display matchup
            id_a, path_a, elo_a, _, _, _ = first_player
            id_b, path_b, elo_b, _, _, _ = second_player

            # Get current rankings
            current_rankings = get_rankings(conn)
            rank_a = current_rankings.get(id_a, "?")
            rank_b = current_rankings.get(id_b, "?")

            # Calculate win probabilities
            prob_a = calculate_win_probability(elo_a, elo_b)
            prob_b = 1.0 - prob_a

            # Display probabilities as percentages, always >= 50%
            if prob_a >= 0.5:
                win_prob_display = f"{prob_a * 100:.0f}% A"
            else:
                win_prob_display = f"{prob_b * 100:.0f}% B"

            display_path_a = display_name(path_a)
            display_path_b = display_name(path_b)

            matchup_display = format_matchup(
                display_path_a, elo_a, rank_a, format_record(first_player),
                display_path_b, elo_b, rank_b, format_record(second_player),
                win_prob_display, prob_a
            )
            print(matchup_display)

            # Get user input
            while True:
                if args.knockout:
                    user_input = input("Your choice (A/B/t/a-/b-/a+/b+/ta-/tb-/t-/o/top [N]/ren <old> <new>/rem a/b/ab/add <name>/reset): ").strip()
                else:
                    user_input = input("Your choice (A/B/t/o/top [N]/ren <old> <new>/rem a/b/ab): ").strip()

                # Check for top command
                top_n = parse_top_command(user_input)
                if top_n is not None:
                    display_leaderboard(conn, top_n, args.target_dir, tournament_pool=tournament_pool)
                    # Re-display the matchup
                    print(matchup_display)
                    continue

                # Check for open command
                if user_input.lower() == 'o':
                    handle_open_command(path_a, path_b, args.target_dir)
                    continue

                # Check for rename command
                if user_input.lower().startswith('ren '):
                    path_a, path_b = handle_rename_command(conn, user_input, args.target_dir,
                                                           pattern, path_a, path_b)
                    display_path_a = display_name(path_a)
                    display_path_b = display_name(path_b)
                    matchup_display = format_matchup(
                        display_path_a, elo_a, rank_a, format_record(first_player),
                        display_path_b, elo_b, rank_b, format_record(second_player),
                        win_prob_display, prob_a
                    )
                    print(matchup_display)
                    continue

                # Check for reset command (knockout mode only)
                if user_input.lower() == 'reset':
                    if handle_reset_command(conn, eliminated, tournament_pool):
                        # Break out of input loop to re-sync and start fresh
                        break
                    else:
                        print(matchup_display)
                    continue

                # Check for rem command
                if user_input.lower().startswith('rem '):
                    arg = user_input[4:].strip()
                    if handle_rem_command(conn, arg, id_a, id_b, path_a, path_b, args.target_dir, files, eliminated, tournament_pool):
                        break
                    continue

                # Check for add command (knockout mode only)
                if user_input.lower().startswith('add '):
                    if not args.knockout:
                        print(red("Error: 'add' command is only available in knockout mode"))
                        continue
                    arg = user_input[4:].strip()
                    if handle_add_command(conn, arg, args.target_dir, pattern, eliminated, tournament_pool):
                        break
                    continue

                # Check for knockout-only commands
                if user_input.upper() in ['A-', 'B-', 'A+', 'B+', 'TA-', 'TB-', 'T-'] and not args.knockout:
                    print(red("Error: a-/b-/a+/b+/ta-/tb-/t- commands only available in knockout mode"))
                    continue

                # Validate input
                if user_input.upper() in ['A', 'B', 'T', 'A-', 'B-', 'A+', 'B+', 'TA-', 'TB-', 'T-']:
                    result = user_input.upper()
                    if result == 'T':
                        result = 'tie'

                    handle_game_result(conn, result, id_a, id_b, elo_a, elo_b,
                                     path_a, path_b, args.target_dir, args.knockout,
                                     eliminated, pattern, tournament_pool)
                    break
                else:
                    if args.knockout:
                        print(yellow("Invalid input. Please enter A, B, t, a-, b-, a+, b+, ta-, tb-, t-, o, top [N], ren <old> <new>, rem a/b/ab, add <name>, or reset"))
                    else:
                        print(yellow("Invalid input. Please enter A, B, t, o, top [N], ren <old> <new>, or rem a/b/ab"))

    except KeyboardInterrupt:
        print(dim("\n\nGoodbye!"))
    finally:
        conn.close()
