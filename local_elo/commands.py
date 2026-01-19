import sqlite3
import sys
import os
import subprocess
import argparse
import random
from typing import Optional, List, Tuple

from . import DEFAULT_LEADERBOARD_SIZE, DEFAULT_ELO
from .db import (
    init_db, sync_files, get_active_files, load_knockout_state,
    save_elimination, clear_knockout_state, get_rankings, remove_entry_from_database,
    get_knockout_stats, export_knockout_results, save_knockout_pool, load_knockout_pool,
    clear_knockout_pool
)
from .elo import calculate_win_probability, record_game, redistribute_elo_delta
from .files import trash_file, apply_wildcard_rename
from .ui import display_leaderboard, display_ranking_changes, format_record
from .game import select_first_player, select_second_player


def parse_top_command(user_input: str) -> Optional[int]:
    """Parse 'top N' command and return the number, or None if not a top command."""
    parts = user_input.strip().lower().split()
    if not parts or parts[0] != 'top':
        return None

    if len(parts) == 1:
        return DEFAULT_LEADERBOARD_SIZE

    try:
        return int(parts[1])
    except (ValueError, IndexError):
        return DEFAULT_LEADERBOARD_SIZE


def handle_rem_command(conn: sqlite3.Connection, arg: str, id_a: int, id_b: int,
                       path_a: str, path_b: str, target_dir: str,
                       files: List[Tuple], eliminated: set, tournament_pool: set) -> bool:
    """
    Remove competitor(s) by reference: 'a', 'b', or 'ab'.
    Returns True to signal need for new matchup.
    """
    arg = arg.lower()
    if arg not in ('a', 'b', 'ab', 'ba'):
        print(f"  Invalid argument: '{arg}'. Use 'a', 'b', or 'ab'.")
        return False

    to_remove = []
    if 'a' in arg:
        to_remove.append((id_a, path_a))
    if 'b' in arg:
        to_remove.append((id_b, path_b))

    for file_id, file_path in to_remove:
        # Get current Elo
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        if not row:
            continue

        file_elo = row[0]
        delta = file_elo - 1000

        # Use existing functions for removal
        full_path = os.path.join(target_dir, file_path) if target_dir != '.' else file_path
        redistribute_elo_delta(conn, delta, file_id)
        trash_file(full_path, target_dir)
        remove_entry_from_database(conn, file_id)

        # Clean up in-memory state
        eliminated.discard(file_id)
        tournament_pool.discard(file_id)

        print(f"✓ Removed {file_path} and redistributed {delta:+.1f} Elo")

    return True


def handle_open_command(path_a: str, path_b: str, target_dir: str) -> None:
    """Handle the 'o' command to open both files."""
    # Construct full paths first
    full_path_a = os.path.join(target_dir, path_a)
    full_path_b = os.path.join(target_dir, path_b)

    # Convert to absolute paths
    abs_path_a = os.path.abspath(full_path_a)
    abs_path_b = os.path.abspath(full_path_b)

    # Check for custom startup script in target directory
    custom_script = None
    if sys.platform in ['darwin', 'linux'] or sys.platform.startswith('linux'):
        # macOS and Linux use .sh
        script_path = os.path.join(target_dir, 'elo_start.sh')
        if os.path.exists(script_path):
            custom_script = script_path
    elif sys.platform == 'win32':
        # Windows uses .bat
        script_path = os.path.join(target_dir, 'elo_start.bat')
        if os.path.exists(script_path):
            custom_script = script_path

    if custom_script:
        # Use custom script with absolute paths as arguments
        # On Unix systems, explicitly invoke with bash; on Windows, run directly
        if sys.platform in ['darwin', 'linux'] or sys.platform.startswith('linux'):
            subprocess.run(['bash', custom_script, abs_path_a])
            subprocess.run(['bash', custom_script, abs_path_b])
        else:
            subprocess.run([custom_script, abs_path_a])
            subprocess.run([custom_script, abs_path_b])
        print(f"Opened {path_a} and {path_b} using {os.path.basename(custom_script)}")
    else:
        # Fall back to default platform commands
        if sys.platform == 'darwin':  # macOS
            open_cmd = 'open'
        elif sys.platform.startswith('linux'):  # Linux
            open_cmd = 'xdg-open'
        elif sys.platform == 'win32':  # Windows
            open_cmd = 'start'
        else:
            print("Unsupported platform for opening files")
            return

        subprocess.run([open_cmd, abs_path_a])
        subprocess.run([open_cmd, abs_path_b])
        print(f"Opened {path_a} and {path_b}")


def handle_rename_command(conn: sqlite3.Connection, user_input: str, target_dir: str,
                          pattern: str, path_a: str, path_b: str) -> Tuple[str, str]:
    """
    Handle the 'ren' command to rename files.
    Returns updated (path_a, path_b) in case one was renamed.
    """
    # Parse rename command: "ren <old> <new>"
    parts = user_input.split(maxsplit=2)
    if len(parts) != 3:
        print("Usage: ren <old_filename> <new_filename>")
        return path_a, path_b

    old_name = parts[1]
    new_name = parts[2]

    # Check if wildcard pattern is used
    if '*' in old_name:
        try:
            matches = apply_wildcard_rename(old_name, new_name, target_dir)
            
            # Validate no conflicts (new filenames don't already exist)
            conflict_found = False
            for old_filename, new_filename in matches:
                new_path = os.path.join(target_dir, new_filename)
                if os.path.exists(new_path):
                    print(f"Error: File '{new_filename}' already exists")
                    conflict_found = True
                    break
            
            if conflict_found:
                return path_a, path_b
            
            # Rename all matching files
            cursor = conn.cursor()
            renamed_count = 0
            for old_filename, new_filename in matches:
                old_path = os.path.join(target_dir, old_filename)
                new_path = os.path.join(target_dir, new_filename)
                
                try:
                    os.rename(old_path, new_path)
                    cursor.execute('UPDATE files SET path = ? WHERE path = ?', (new_filename, old_filename))
                    renamed_count += 1
                    
                    # Update current matchup if one of the files was renamed
                    if path_a == old_filename:
                        path_a = new_filename
                    if path_b == old_filename:
                        path_b = new_filename
                except OSError as e:
                    print(f"Error renaming '{old_filename}' to '{new_filename}': {e}")
            
            conn.commit()
            print(f"Renamed {renamed_count} file(s)")
            
        except ValueError as e:
            print(f"Error: {e}")
        
        # Re-sync to refresh the files list
        sync_files(conn, pattern, target_dir)
        return path_a, path_b
    else:
        # Single file rename (existing logic)
        # Build full paths
        old_path = os.path.join(target_dir, old_name)
        new_path = os.path.join(target_dir, new_name)

        # Validate old file exists
        if not os.path.exists(old_path):
            print(f"Error: File '{old_name}' not found")
            return path_a, path_b

        # Check if new file already exists
        if os.path.exists(new_path):
            print(f"Error: File '{new_name}' already exists")
            return path_a, path_b

        # Rename in filesystem
        try:
            os.rename(old_path, new_path)
        except OSError as e:
            print(f"Error renaming file: {e}")
            return path_a, path_b

        # Update database
        cursor = conn.cursor()
        cursor.execute('UPDATE files SET path = ? WHERE path = ?', (new_name, old_name))
        conn.commit()

        print(f"Renamed '{old_name}' to '{new_name}'")

        # Update current matchup if one of the files was renamed
        if path_a == old_name:
            path_a = new_name
        if path_b == old_name:
            path_b = new_name

        # Re-sync to refresh the files list
        sync_files(conn, pattern, target_dir)
        return path_a, path_b


def handle_reset_command(conn: sqlite3.Connection, eliminated: set, tournament_pool: set) -> bool:
    """
    Handle the 'reset' command in knockout mode.
    Returns True if should break out of input loop to re-sync.
    """
    # Ask for confirmation
    confirm = input("Are you sure you want to reset the knockout tournament? All eliminations will be cleared. (y/N): ").strip().lower()
    if confirm == 'y' or confirm == 'yes':
        clear_knockout_state(conn)
        clear_knockout_pool(conn)
        eliminated.clear()  # Clear in-memory set
        tournament_pool.clear()  # Clear in-memory set
        print("Knockout tournament has been reset! All players are back in.\n")
        return True
    else:
        print("Reset cancelled.\n")
        return False


def handle_game_result(conn: sqlite3.Connection, result: str, id_a: int, id_b: int,
                       elo_a: float, elo_b: float, path_a: str, path_b: str,
                       target_dir: str, knockout_mode: bool, eliminated: set,
                       pattern: str, tournament_pool: set) -> None:
    """
    Handle game result input (A, B, t, a-, b-, a+, b+, ta-, tb-, t-).
    Records the game, updates rankings, and handles knockout eliminations.
    """
    # Get rankings before the game
    old_rankings = get_rankings(conn)

    # Record the game (normalize commands with -/+ to base result for Elo calculation)
    if result in ['A-', 'B-', 'A+', 'B+']:
        game_result = result.rstrip('-+')
    elif result in ['TA-', 'TB-', 'T-']:
        game_result = 'tie'
    else:
        game_result = result
    record_game(conn, id_a, id_b, elo_a, elo_b, game_result)

    # Display ranking changes
    display_ranking_changes(conn, old_rankings, id_a, id_b, target_dir)

    # In knockout mode, eliminate the loser (or winner if a-/b- used) and persist to database
    if knockout_mode:
        remove_winner = result in ['A-', 'B-']
        keep_loser = result in ['A+', 'B+']

        if result in ['A', 'A-', 'A+']:
            if remove_winner:
                eliminated.add(id_a)
                save_elimination(conn, id_a)
                print(f"  {path_a} wins but is REMOVED from tournament!\n")
            elif keep_loser:
                # Winner wins, but loser stays
                print(f"  {path_a} wins, but both players stay in tournament!\n")
            else:
                eliminated.add(id_b)
                save_elimination(conn, id_b)
                print(f"  {path_b} has been ELIMINATED!\n")
        elif result in ['B', 'B-', 'B+']:
            if remove_winner:
                eliminated.add(id_b)
                save_elimination(conn, id_b)
                print(f"  {path_b} wins but is REMOVED from tournament!\n")
            elif keep_loser:
                # Winner wins, but loser stays
                print(f"  {path_b} wins, but both players stay in tournament!\n")
            else:
                eliminated.add(id_a)
                save_elimination(conn, id_a)
                print(f"  {path_a} has been ELIMINATED!\n")
        elif result == 'TA-':
            # Tie but eliminate A
            eliminated.add(id_a)
            save_elimination(conn, id_a)
            print(f"  Tie, but {path_a} is REMOVED from tournament!\n")
        elif result == 'TB-':
            # Tie but eliminate B
            eliminated.add(id_b)
            save_elimination(conn, id_b)
            print(f"  Tie, but {path_b} is REMOVED from tournament!\n")
        elif result == 'T-':
            # Tie but eliminate both
            eliminated.add(id_a)
            eliminated.add(id_b)
            save_elimination(conn, id_a)
            save_elimination(conn, id_b)
            print(f"  Tie, but BOTH players are REMOVED from tournament!\n")
        # In case of regular tie, no one is eliminated
        else:
            print("  Tie - no one eliminated.\n")

        # Show remaining players count
        if tournament_pool:
            remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                                  if f[0] in tournament_pool and f[0] not in eliminated])
        else:
            remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                                  if f[0] not in eliminated])
        print(f"Players remaining: {remaining_count}\n")


def main():
    """Main entry point for the Local Elo CLI tool."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Local Elo - Rank files using Elo ratings')
    parser.add_argument('target_dir', nargs='?', default='.',
                       help='Target directory to search for files (default: current directory)')
    parser.add_argument('-m', '--match', dest='pattern', default='.*',
                       help='Regex pattern for matching files (default: match all files)')
    parser.add_argument('-k', '--knockout', action='store_true',
                       help='Knockout mode: eliminate losers until only one remains')
    parser.add_argument('-p', '--power', dest='power', type=float, default=1.0,
                       help='Power law exponent for games-played balancing (default: 1.0; higher values more aggressively favor underplayed entries)')
    parser.add_argument('-n', '--pool-size', dest='pool_size', type=int, default=None,
                       help='Limit pool size for competitor selection in knockout mode (default: use all remaining files)')
    args = parser.parse_args()

    # Validate power parameter
    if args.power <= 0:
        print("Error: Power parameter must be positive (e.g., 0.5, 1.0, 2.0)")
        sys.exit(1)

    # Validate pool_size parameter
    if args.pool_size is not None and args.pool_size < 2:
        print("Error: Pool size must be at least 2")
        sys.exit(1)

    # Initialize database
    conn = init_db(args.target_dir)

    try:
        # Initialize eliminated set and tournament pool
        if args.knockout:
            # Load existing knockout state from database
            eliminated = load_knockout_state(conn)
            tournament_pool = load_knockout_pool(conn)

            if eliminated or tournament_pool:
                # Existing tournament in progress
                if args.pool_size:
                    # User specified pool size but tournament already exists
                    pool_count = len(tournament_pool) if tournament_pool else None
                    if pool_count and pool_count != args.pool_size:
                        print(f"ERROR: Existing knockout tournament has pool size {pool_count}, but you specified -n {args.pool_size}")
                        print("Options:")
                        print("  1. Continue without -n flag to resume existing tournament")
                        print("  2. Run 'reset' command to start a new tournament with new pool size")
                        sys.exit(1)

                # Resume existing tournament
                stats = get_knockout_stats(conn, args.target_dir, args.pattern)
                competing_count = len(tournament_pool) - len(eliminated) if tournament_pool else stats['competing_count']
                print(f"Resuming knockout tournament...")
                if tournament_pool:
                    print(f"  Tournament pool size: {len(tournament_pool)}")
                print(f"  Total files in database: {stats['total_count']}")
                print(f"  Already eliminated: {stats['eliminated_count']}")
                print(f"  Still competing: {competing_count}")
                print()
            else:
                # New tournament - initialize pool if specified
                if args.pool_size:
                    all_files = get_active_files(conn, args.target_dir, args.pattern)
                    if len(all_files) < args.pool_size:
                        print(f"ERROR: Only {len(all_files)} files available, but pool size is {args.pool_size}")
                        sys.exit(1)

                    # Select pool using weighted selection
                    pool_weights = []
                    for f in all_files:
                        elo_weight = calculate_win_probability(f[2], DEFAULT_ELO)
                        games_played = f[3] + f[4] + f[5]
                        games_weight = 1.0 / ((games_played + 1) ** args.power)
                        pool_weights.append(elo_weight * games_weight)

                    selected_files = random.choices(all_files, weights=pool_weights, k=args.pool_size)
                    tournament_pool = {f[0] for f in selected_files}  # Extract file IDs
                    save_knockout_pool(conn, tournament_pool)
                    print(f"Selected {args.pool_size} competitors for knockout tournament")
                    print()
                else:
                    tournament_pool = set()  # Empty = all files participate
        else:
            # Not in knockout mode, but keep variables for consistency
            eliminated = set()
            tournament_pool = set()

        if args.knockout:
            print("Local Elo - File Ranking Tool (KNOCKOUT MODE)")
            print("Commands: A (file A wins), B (file B wins), a-/b- (win but remove winner), a+/b+ (win but loser stays), t (tie), ta-/tb-/t- (tie but eliminate a/b/both), o (open files), top [N] (show leaderboard), ren <old> <new> (rename file), rem a/b/ab (remove entry)")
            print("Note: Losers are eliminated! Last one standing wins.")
            print("Press Ctrl+C to exit\n")
        else:
            print("Local Elo - File Ranking Tool")
            print("Commands: A (file A wins), B (file B wins), t (tie), o (open files), top [N] (show leaderboard), ren <old> <new> (rename file), rem a/b/ab (remove entry)")
            print("Press Ctrl+C to exit\n")

        while True:
            # Sync files with database
            sync_files(conn, args.pattern, args.target_dir)

            # Get active files
            files = get_active_files(conn, args.target_dir, args.pattern)

            # In knockout mode, filter by tournament pool and eliminated players
            if args.knockout:
                if tournament_pool:
                    # Only include files in the tournament pool
                    files = [f for f in files if f[0] in tournament_pool and f[0] not in eliminated]
                else:
                    # No pool restriction, just filter eliminated
                    files = [f for f in files if f[0] not in eliminated]

            if len(files) == 0:
                print("No files found matching the pattern.")
                break

            if len(files) == 1:
                if args.knockout:
                    # Winner found in knockout mode - show complete tournament results
                    print(f"\n{'='*60}")
                    print("KNOCKOUT TOURNAMENT COMPLETE!")
                    print(f"{'='*60}\n")

                    # Count total files in database for limit
                    cursor = conn.cursor()
                    cursor.execute('SELECT COUNT(*) FROM files')
                    total_files_count = cursor.fetchone()[0]

                    # Display elimination-order leaderboard for all tournament participants
                    display_leaderboard(
                        conn,
                        limit=total_files_count,
                        target_dir=args.target_dir,
                        sort_by='knockout',
                        show_all_files=True,
                        pattern=args.pattern
                    )

                    # Prompt user for reset or quit
                    print("Type 'reset' to start a new tournament and export results to CSV, or 'q' to quit.")

                    should_exit = False
                    while True:
                        user_input = input("> ").strip().lower()
                        if user_input == 'reset':
                            # Export results to CSV
                            csv_path = export_knockout_results(conn, args.target_dir)
                            print(f"\nResults exported to: {csv_path}\n")

                            # Clear knockout state and restart
                            clear_knockout_state(conn)
                            clear_knockout_pool(conn)
                            eliminated.clear()
                            tournament_pool.clear()
                            print("Knockout tournament reset! All players are back in.\n")
                            break  # Exit input loop, continue to next matchup
                        elif user_input in ['q', 'quit']:
                            should_exit = True
                            break  # Exit input loop AND signal main loop to exit
                        else:
                            print("Invalid input. Please type 'reset' or 'q'.\n")

                    if should_exit:
                        break  # Exit main comparison loop
                    # Otherwise, continue to next matchup
                else:
                    print("Only one file found. Need at least two files for comparison.")
                    break

            # Select two players
            first_player = select_first_player(files, args.power)
            second_player = select_second_player(files, first_player)

            if second_player is None:
                print("Could not find a second player.")
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

            # Display full path if not in current directory
            display_path_a = os.path.join(args.target_dir, path_a) if args.target_dir != '.' else path_a
            display_path_b = os.path.join(args.target_dir, path_b) if args.target_dir != '.' else path_b

            # Build matchup display string
            matchup_display = f"A: {display_path_a} ({int(elo_a)} / #{rank_a} / {format_record(first_player)})\nvs\nB: {display_path_b} ({int(elo_b)} / #{rank_b} / {format_record(second_player)})\nWin probability: {win_prob_display}"
            print(matchup_display)

            # Get user input
            while True:
                if args.knockout:
                    user_input = input("Your choice (A/B/t/a-/b-/a+/b+/ta-/tb-/t-/o/top [N]/ren <old> <new>/rem a/b/ab/reset): ").strip()
                else:
                    user_input = input("Your choice (A/B/t/o/top [N]/ren <old> <new>/rem a/b/ab): ").strip()

                # Check for top command
                top_n = parse_top_command(user_input)
                if top_n is not None:
                    display_leaderboard(conn, top_n, args.target_dir)
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
                                                           args.pattern, path_a, path_b)
                    # Re-display matchup with updated paths
                    display_path_a = os.path.join(args.target_dir, path_a) if args.target_dir != '.' else path_a
                    display_path_b = os.path.join(args.target_dir, path_b) if args.target_dir != '.' else path_b
                    matchup_display = f"A: {display_path_a} ({int(elo_a)} / #{rank_a} / {format_record(first_player)})\nvs\nB: {display_path_b} ({int(elo_b)} / #{rank_b} / {format_record(second_player)})\nWin probability: {win_prob_display}"
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

                # Check for knockout-only commands
                if user_input.upper() in ['A-', 'B-', 'A+', 'B+', 'TA-', 'TB-', 'T-'] and not args.knockout:
                    print("Error: a-/b-/a+/b+/ta-/tb-/t- commands only available in knockout mode")
                    continue

                # Validate input
                if user_input.upper() in ['A', 'B', 'T', 'A-', 'B-', 'A+', 'B+', 'TA-', 'TB-', 'T-']:
                    result = user_input.upper()
                    if result == 'T':
                        result = 'tie'

                    handle_game_result(conn, result, id_a, id_b, elo_a, elo_b,
                                     path_a, path_b, args.target_dir, args.knockout,
                                     eliminated, args.pattern, tournament_pool)
                    break
                else:
                    if args.knockout:
                        print("Invalid input. Please enter A, B, t, a-, b-, a+, b+, ta-, tb-, t-, o, top [N], ren <old> <new>, rem a/b/ab, or reset")
                    else:
                        print("Invalid input. Please enter A, B, t, o, top [N], ren <old> <new>, or rem a/b/ab")

    except KeyboardInterrupt:
        print("\n\nGoodbye!")
    finally:
        conn.close()
