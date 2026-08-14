import sqlite3
import sys
import os

from .db import init_db, get_active_files, get_rankings, load_round_played, clear_round_played, get_round_info, set_round_info, get_tags, get_tag_color_map
from .files import handle_open_command, handle_rename_command, handle_rem_command, handle_add_command, handle_refresh_command, handle_tag_command, sync_files
from .ui import display_leaderboard, format_record, parse_top_command, display_welcome_message, format_competition
from .game import select_match_players
from .knockout import (
    handle_game_result, handle_reset_command, initialize_knockout_tournament, handle_winner_screen,
    effective_locked, handle_all_locked_unlock, handle_lock_command
)
from .colors import red, yellow, green, dim
from .utils import display_name, extensions_to_pattern
from .outcome import parse_outcome_command, parse_outcome_with_lock_modifiers, slot_letters
from .settings import resolve_settings, settings_to_config, save_config, build_parser


def main():
    """Main entry point for the Local Elo CLI tool."""
    # `merge` is a dedicated subcommand with its own parser; dispatch before the
    # flat settings machinery (which assumes a positional target_dir) ever runs.
    if len(sys.argv) > 1 and sys.argv[1] == 'merge':
        from .merge import merge_main
        sys.exit(merge_main(sys.argv[2:]))

    # Resolve settings: built-in defaults < stored JSON (target dir) < CLI flags.
    args = resolve_settings()

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
    if args.match_size < 2:
        print(red("Error: match size must be at least 2"))
        sys.exit(1)
    if args.match_size > 26:
        print(red("Error: match size cannot exceed 26"))
        sys.exit(1)

    # Initialize database
    conn = init_db(args.target_dir)

    # One-shot refresh: clean stale DB entries, recalculate Elos, then exit.
    # Note: we deliberately do NOT sync_files here — refresh only removes entries
    # whose files are gone, it does not add newly-present files.
    if args.refresh:
        handle_refresh_command(conn, args.target_dir)
        conn.close()
        return

    try:
        if args.knockout:
            eliminated, tournament_pool, locked = initialize_knockout_tournament(
                conn, args.target_dir, pattern, args.pool_size, args.power
            )
        else:
            eliminated = set()
            tournament_pool = set()
            locked = set()

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
                        conn, args.target_dir, pattern, eliminated, tournament_pool, locked
                    )
                    if should_exit:
                        break
                else:
                    print(yellow("Only one file found. Need at least two files for comparison."))
                    break

            # Select players for this competition
            if args.knockout:
                active_ids = {f[0] for f in files}
                handle_all_locked_unlock(conn, active_ids, locked)
                round_played = load_round_played(conn)
                locked_effective = effective_locked(active_ids, locked, args.match_size)
                eligible = [f for f in files if f[0] not in round_played and f[0] not in locked_effective]

                min_required = min(args.match_size, len(files))
                if len(eligible) < min_required:
                    clear_round_played(conn)
                    round_played = set()
                    locked_effective = effective_locked(active_ids, locked, args.match_size)
                    eligible = [f for f in files if f[0] not in round_played and f[0] not in locked_effective]
                    if len(eligible) < min_required:
                        if locked_effective:
                            print(dim("Locked players are needed to fill this matchup size — ignoring locks this round."))
                        eligible = [f for f in files if f[0] not in round_played]
                    round_num, _ = get_round_info(conn)
                    round_num += 1
                    set_round_info(conn, round_num, len(eligible))
                    print(dim(f"All remaining players have played — starting round {round_num}.\n"))

                round_num, round_total = get_round_info(conn)
                if round_total is None:
                    set_round_info(conn, round_num, len(eligible))
                    round_total = len(eligible)
                promoted = len(round_played)
                locked_count = len(active_ids & locked)
                print(dim(f"Round {round_num} — {len(eligible)}/{round_total} players left, {promoted} promoted, {locked_count} locked"))

                competition_players = select_match_players(eligible, args.match_size, args.power)
            else:
                competition_players = select_match_players(files, args.match_size, args.power)

            if len(competition_players) < 2:
                print(red("Could not find enough players for this competition."))
                break

            # Get current rankings
            current_rankings = get_rankings(conn)
            slots = slot_letters(len(competition_players))

            def build_matchup_rows(players):
                rows = []
                for idx, player in enumerate(players):
                    player_id, path, elo, _, _, _ = player
                    rows.append((
                        slots[idx],
                        display_name(path),
                        elo,
                        current_rankings.get(player_id, "?"),
                        format_record(player),
                        get_tags(conn, player_id),
                    ))
                return rows

            def render_matchup(players):
                # Recompute the tag color map each render so tags applied this
                # session pick up their (stable, timestamp-ordered) color.
                return format_competition(build_matchup_rows(players), get_tag_color_map(conn))

            matchup_display = render_matchup(competition_players)
            print(matchup_display)

            # Get user input
            while True:
                slot_hint = "".join(slots)
                if args.knockout:
                    user_input = input(
                        f"Your choice ({slot_hint}[+/-/!]/t/o/top [N]/ren <letter|old> <new>/rem <slots>/tag <letter> <±tags>/add <name>/refresh/reset/store): "
                    ).strip()
                else:
                    user_input = input(
                        f"Your choice ({slot_hint}/t/o/top [N]/ren <letter|old> <new>/rem <slots>/tag <letter> <±tags>/refresh/store): "
                    ).strip()

                # Check for top command
                top_n = parse_top_command(user_input)
                if top_n is not None:
                    display_leaderboard(conn, top_n, args.target_dir, tournament_pool=tournament_pool)
                    # Re-display the matchup
                    print(matchup_display)
                    continue

                # Check for open command
                if user_input.lower() == 'o':
                    handle_open_command([p[1] for p in competition_players], args.target_dir)
                    continue

                # Check for rename command
                if user_input.lower().startswith('ren '):
                    updated_paths = handle_rename_command(
                        conn,
                        user_input,
                        args.target_dir,
                        pattern,
                        [p[1] for p in competition_players],
                    )
                    competition_players = [
                        (player[0], updated_paths[idx], player[2], player[3], player[4], player[5])
                        for idx, player in enumerate(competition_players)
                    ]
                    matchup_display = render_matchup(competition_players)
                    print(matchup_display)
                    continue

                # Check for refresh command
                if user_input.lower() == 'refresh':
                    removed = handle_refresh_command(
                        conn, args.target_dir, eliminated, tournament_pool, locked
                    )
                    if removed:
                        # Survivor Elos changed — re-sync and pull a fresh matchup.
                        break
                    print(matchup_display)
                    continue

                # Check for store command: persist the current settings to the
                # target dir's JSON config so future runs pick them up by default.
                if user_input.lower() == 'store':
                    path = save_config(args.target_dir, settings_to_config(args, build_parser()))
                    print(green(f"Saved current settings to {path}"))
                    print(matchup_display)
                    continue

                # Check for reset command (knockout mode only)
                if user_input.lower() == 'reset':
                    if handle_reset_command(conn, eliminated, tournament_pool, locked):
                        # Break out of input loop to re-sync and start fresh
                        break
                    else:
                        print(matchup_display)
                    continue

                # Check for rem command
                if user_input.lower().startswith('rem '):
                    arg = user_input[4:].strip()
                    visible_competitors = [(player[0], player[1]) for player in competition_players]
                    removed_ids = handle_rem_command(conn, arg, visible_competitors, args.target_dir, files, eliminated, tournament_pool)
                    if not removed_ids:
                        continue
                    remaining = [p for p in competition_players if p[0] not in removed_ids]
                    # Multiplayer match with >=2 survivors: keep playing this matchup instead of resetting.
                    if len(competition_players) > 2 and len(remaining) >= 2:
                        # Removal redistributed Elo to survivors, so re-fetch their fresh rows
                        # (handle_game_result scores off player[2]).
                        fresh = {f[0]: f for f in get_active_files(conn, args.target_dir, pattern)}
                        competition_players = [fresh.get(p[0], p) for p in remaining]
                        current_rankings = get_rankings(conn)
                        slots = slot_letters(len(competition_players))
                        matchup_display = render_matchup(competition_players)
                        print(matchup_display)
                        continue
                    break

                # Check for add command (knockout mode only)
                if user_input.lower().startswith('add '):
                    if not args.knockout:
                        print(red("Error: 'add' command is only available in knockout mode"))
                        continue
                    arg = user_input[4:].strip()
                    if handle_add_command(conn, arg, args.target_dir, pattern, eliminated, tournament_pool):
                        break
                    continue

                # Check for tag command
                if user_input.lower().startswith('tag '):
                    arg = user_input[4:].strip()
                    visible_competitors = [(player[0], player[1]) for player in competition_players]
                    handle_tag_command(conn, arg, visible_competitors)
                    # Tags changed — rebuild the matchup block so they show immediately.
                    matchup_display = render_matchup(competition_players)
                    print(matchup_display)
                    continue

                # Check for knockout-only pass modifiers
                lowered = user_input.strip().lower()
                if not args.knockout and (lowered.endswith('+') or lowered.endswith('-')):
                    print(red("Error: +/- commands are only available in knockout mode"))
                    continue

                try:
                    if args.knockout:
                        outcome, lock_slots = parse_outcome_with_lock_modifiers(user_input, len(competition_players))
                        lock_arg = "".join(slots[idx] for idx in sorted(lock_slots))
                    else:
                        outcome = parse_outcome_command(user_input, len(competition_players))
                        lock_arg = ""
                except ValueError as exc:
                    if args.knockout:
                        print(yellow(f"Invalid input: {exc}. Use slots ({slot_hint}) with optional +, -, and inline !, t, o, top [N], ren, rem, tag, add, refresh, reset, or store"))
                    else:
                        print(yellow(f"Invalid input: {exc}. Use slots ({slot_hint}), t, o, top [N], ren, rem, tag, refresh, or store"))
                    continue

                handle_game_result(
                    conn,
                    outcome,
                    competition_players,
                    args.target_dir,
                    args.knockout,
                    eliminated,
                    pattern,
                    tournament_pool,
                )
                if lock_arg:
                    visible_competitors = [(player[0], player[1]) for player in competition_players]
                    handle_lock_command(conn, lock_arg, visible_competitors, locked)

                # The "-" suffix removes the winner(s) from the tournament but
                # keeps the remaining players competing in the same set — just
                # like `rem`, we replay this matchup instead of drawing a new one.
                if outcome.stay_in_match:
                    remaining = [p for p in competition_players if p[0] not in eliminated]
                    if len(competition_players) > 2 and len(remaining) >= 2:
                        # Scoring redistributed Elo to survivors, so re-fetch their fresh rows.
                        fresh = {f[0]: f for f in get_active_files(conn, args.target_dir, pattern)}
                        competition_players = [fresh.get(p[0], p) for p in remaining]
                        current_rankings = get_rankings(conn)
                        slots = slot_letters(len(competition_players))
                        matchup_display = render_matchup(competition_players)
                        print(matchup_display)
                        continue
                break

    except KeyboardInterrupt:
        print(dim("\n\nGoodbye!"))
    finally:
        conn.close()
