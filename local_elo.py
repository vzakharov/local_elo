#!/usr/bin/env python3
"""Local Elo - A CLI tool for ranking files using Elo ratings through pairwise comparisons."""

import sqlite3
import re
import random
import sys
import os
import subprocess
import argparse
from typing import List, Tuple, Optional

# Global Constants
DEFAULT_ELO = 1000
K_FACTOR = 32
DEFAULT_LEADERBOARD_SIZE = 10
DB_NAME = "local_elo.db"


def init_db(target_dir: str = '.') -> sqlite3.Connection:
    """Initialize the SQLite database and create tables if they don't exist."""
    db_path = os.path.join(target_dir, DB_NAME)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create files table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            elo REAL NOT NULL DEFAULT 1000,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            ties INTEGER DEFAULT 0
        )
    ''')

    # Create games table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY,
            file_a_id INTEGER,
            file_b_id INTEGER,
            result TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_a_id) REFERENCES files(id),
            FOREIGN KEY (file_b_id) REFERENCES files(id)
        )
    ''')

    # Create knockout_state table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knockout_state (
            file_id INTEGER PRIMARY KEY,
            eliminated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    ''')

    conn.commit()
    return conn


def discover_files(pattern: str, target_dir: str = '.') -> List[str]:
    """
    Discover files in the target directory matching the regex pattern.
    Excludes the script itself, the database file, and hidden/system files.
    """
    files = []
    print(f"Discovering files in {target_dir} with pattern {pattern}")
    regex = re.compile(pattern)

    for filename in os.listdir(target_dir):
        # Skip directories
        if os.path.isdir(os.path.join(target_dir, filename)):
            continue

        # Skip hidden/system files (starting with .)
        if filename.startswith('.'):
            continue

        # Skip the script itself, database, and startup scripts
        if filename in ['local_elo.py', DB_NAME, 'elo_start.sh', 'elo_start.bat']:
            continue

        # Check if filename matches the pattern
        if regex.search(filename):
            files.append(filename)

    return files


def add_file_to_db(conn: sqlite3.Connection, filepath: str) -> None:
    """Add a new file to the database with default Elo rating."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO files (path, elo) VALUES (?, ?)',
            (filepath, DEFAULT_ELO)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # File already exists in database
        pass


def sync_files(conn: sqlite3.Connection, pattern: str, target_dir: str = '.') -> None:
    """Sync discovered files with the database."""
    files = discover_files(pattern, target_dir)
    for filepath in files:
        add_file_to_db(conn, filepath)


def calculate_win_probability(elo_a: float, elo_b: float) -> float:
    """Calculate the probability of player A beating player B using Elo formula."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def format_record_values(wins: int, losses: int, ties: int) -> str:
    """Format W/T/L record from individual values."""
    return f"{wins}W-{losses}L-{ties}T"


def format_record(player: tuple) -> str:
    """
    Format a W/T/L record string from a player tuple.

    Args:
        player: Tuple in format (id, path, elo, wins, losses, ties)

    Returns:
        Formatted string like "12W-8L-2T"
    """
    return format_record_values(player[3], player[4], player[5])


def update_elo_ratings(conn: sqlite3.Connection, file_a_id: int, file_b_id: int,
                       elo_a: float, elo_b: float, result: str) -> Tuple[float, float]:
    """
    Update Elo ratings based on the game result.
    Returns the new Elo ratings for both files.
    """
    # Calculate expected scores
    expected_a = calculate_win_probability(elo_a, elo_b)
    expected_b = 1.0 - expected_a

    # Determine actual scores
    if result == 'A':
        actual_a, actual_b = 1.0, 0.0
    elif result == 'B':
        actual_a, actual_b = 0.0, 1.0
    else:  # tie
        actual_a, actual_b = 0.5, 0.5

    # Calculate new ratings
    new_elo_a = elo_a + K_FACTOR * (actual_a - expected_a)
    new_elo_b = elo_b + K_FACTOR * (actual_b - expected_b)

    return new_elo_a, new_elo_b


def record_game(conn: sqlite3.Connection, file_a_id: int, file_b_id: int,
                elo_a: float, elo_b: float, result: str) -> None:
    """Record a game and update Elo ratings."""
    cursor = conn.cursor()

    # Update Elo ratings
    new_elo_a, new_elo_b = update_elo_ratings(conn, file_a_id, file_b_id, elo_a, elo_b, result)

    # Update stats based on result
    if result == 'A':
        cursor.execute('UPDATE files SET elo = ?, wins = wins + 1 WHERE id = ?', (new_elo_a, file_a_id))
        cursor.execute('UPDATE files SET elo = ?, losses = losses + 1 WHERE id = ?', (new_elo_b, file_b_id))
    elif result == 'B':
        cursor.execute('UPDATE files SET elo = ?, losses = losses + 1 WHERE id = ?', (new_elo_a, file_a_id))
        cursor.execute('UPDATE files SET elo = ?, wins = wins + 1 WHERE id = ?', (new_elo_b, file_b_id))
    else:  # tie
        cursor.execute('UPDATE files SET elo = ?, ties = ties + 1 WHERE id = ?', (new_elo_a, file_a_id))
        cursor.execute('UPDATE files SET elo = ?, ties = ties + 1 WHERE id = ?', (new_elo_b, file_b_id))

    # Record the game
    cursor.execute(
        'INSERT INTO games (file_a_id, file_b_id, result) VALUES (?, ?, ?)',
        (file_a_id, file_b_id, result)
    )

    conn.commit()


def load_knockout_state(conn: sqlite3.Connection) -> set:
    """Load eliminated file IDs from database."""
    cursor = conn.cursor()
    cursor.execute('SELECT file_id FROM knockout_state')
    eliminated_ids = {row[0] for row in cursor.fetchall()}
    return eliminated_ids


def save_elimination(conn: sqlite3.Connection, file_id: int) -> None:
    """Mark a file as eliminated in the database."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO knockout_state (file_id) VALUES (?)',
            (file_id,)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # File already eliminated (shouldn't happen, but handle gracefully)
        pass


def clear_knockout_state(conn: sqlite3.Connection) -> None:
    """Clear all knockout state from database."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_state')
    conn.commit()


def get_knockout_stats(conn: sqlite3.Connection, target_dir: str = '.', pattern: str = '.*') -> dict:
    """Get statistics about knockout state."""
    cursor = conn.cursor()

    # Count eliminated players
    cursor.execute('SELECT COUNT(*) FROM knockout_state')
    eliminated_count = cursor.fetchone()[0]

    # Get all active files (files that exist in database and on disk)
    all_active_files = get_active_files(conn, target_dir, pattern)

    # Load eliminated IDs to filter them out
    eliminated_ids = load_knockout_state(conn)

    # Count files still competing (active files minus eliminated)
    competing_count = len([f for f in all_active_files if f[0] not in eliminated_ids])

    # Total is all files that exist on disk
    total_count = len(all_active_files)

    return {
        'eliminated_count': eliminated_count,
        'competing_count': competing_count,
        'total_count': total_count
    }


def get_active_files(conn: sqlite3.Connection, target_dir: str = '.', pattern: str = '.*') -> List[Tuple[int, str, float, int, int, int]]:
    """Get all files that still exist in the filesystem and match the pattern."""
    cursor = conn.cursor()
    cursor.execute('SELECT id, path, elo, wins, losses, ties FROM files')
    all_files = cursor.fetchall()

    regex = re.compile(pattern)

    # Filter to only files that still exist and match the pattern
    active_files = [f for f in all_files if os.path.exists(os.path.join(target_dir, f[1])) and regex.search(f[1])]
    return active_files


def select_first_player(files: List[Tuple[int, str, float, int, int, int]],
                        power: float = 1.0) -> Tuple[int, str, float, int, int, int]:
    """
    Select the first player using weighted random selection.
    Combines two weights:
    1. Elo-based weight: probability of beating an average opponent (DEFAULT_ELO)
    2. Games-played weight: 1 / (games_played + 1)^power to balance selection frequency

    The power parameter controls aggressiveness of games-played balancing:
    - power=0.5: Gentler balancing (square root decay)
    - power=1.0: Standard linear balancing (default)
    - power=2.0: Aggressive quadratic decay
    - power>2.0: Very aggressive (strongly favor least-played entries)

    Combined weight = elo_weight * games_weight
    """
    weights = []
    for f in files:
        # Calculate Elo-based weight
        elo_weight = calculate_win_probability(f[2], DEFAULT_ELO)

        # Calculate games-played weight to balance play frequency
        games_played = f[3] + f[4] + f[5]  # wins + losses + ties
        games_weight = 1.0 / ((games_played + 1) ** power)

        # Combine weights multiplicatively
        combined_weight = elo_weight * games_weight
        weights.append(combined_weight)

    return random.choices(files, weights=weights, k=1)[0]


def select_second_player(files: List[Tuple[int, str, float, int, int, int]],
                        first_player: Tuple[int, str, float, int, int, int]) -> Tuple[int, str, float, int, int, int]:
    """
    Select the second player using weighted random selection.
    Weight = probability of weaker player beating stronger player (encourages close matches).
    """
    # Remove first player from candidates
    candidates = [f for f in files if f[0] != first_player[0]]

    if not candidates:
        return None

    # Calculate weights based on match closeness
    weights = []
    for candidate in candidates:
        # Determine who is weaker/stronger
        if first_player[2] > candidate[2]:
            # First player is stronger
            weight = calculate_win_probability(candidate[2], first_player[2])
        else:
            # Candidate is stronger
            weight = calculate_win_probability(first_player[2], candidate[2])
        weights.append(weight)

    return random.choices(candidates, weights=weights, k=1)[0]


def get_rankings(conn: sqlite3.Connection) -> dict:
    """Get current rankings as a dictionary mapping file_id to rank position."""
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM files ORDER BY elo DESC')
    results = cursor.fetchall()

    rankings = {}
    for rank, (file_id,) in enumerate(results, 1):
        rankings[file_id] = rank

    return rankings


def display_leaderboard(conn: sqlite3.Connection, limit: int = DEFAULT_LEADERBOARD_SIZE, target_dir: str = '.') -> None:
    """Display the top N files by Elo rating."""
    cursor = conn.cursor()
    cursor.execute(
        'SELECT path, elo, wins, losses, ties FROM files ORDER BY elo DESC LIMIT ?',
        (limit,)
    )
    results = cursor.fetchall()

    print(f"\nTop {limit} Files:")
    for i, (path, elo, wins, losses, ties) in enumerate(results, 1):
        # Display full path if not in current directory
        display_path = os.path.join(target_dir, path) if target_dir != '.' else path
        print(f"{i}. {int(elo)} ({format_record_values(wins, losses, ties)}) {display_path}")
    print()


def display_ranking_changes(conn: sqlite3.Connection, old_rankings: dict,
                           file_a_id: int, file_b_id: int, target_dir: str = '.') -> None:
    """Display ranking changes for the two files that just competed."""
    cursor = conn.cursor()

    # Get new rankings
    new_rankings = get_rankings(conn)

    # Get file info for both players with new Elo ratings
    cursor.execute('SELECT id, path, elo FROM files WHERE id IN (?, ?)', (file_a_id, file_b_id))
    files = cursor.fetchall()

    print("\nRankings:")
    for file_id, path, new_elo in files:
        old_rank = old_rankings.get(file_id, "N/A")
        new_rank = new_rankings.get(file_id, "N/A")

        if old_rank == new_rank:
            movement = f"#{new_rank} (no change)"
        elif old_rank == "N/A":
            movement = f"#{new_rank} (new)"
        elif new_rank == "N/A":
            movement = f"unranked (was #{old_rank})"
        elif old_rank > new_rank:
            movement = f"#{new_rank} (up from #{old_rank})"
        else:
            movement = f"#{new_rank} (down from #{old_rank})"

        # Display full path if not in current directory
        display_path = os.path.join(target_dir, path) if target_dir != '.' else path
        print(f"  {display_path}: {movement} | New Elo: {int(new_elo)}")
    print()


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
    parser.add_argument('--reset-knockout', action='store_true',
                       help='Clear knockout state and start fresh')
    args = parser.parse_args()

    # Validate power parameter
    if args.power <= 0:
        print("Error: Power parameter must be positive (e.g., 0.5, 1.0, 2.0)")
        sys.exit(1)

    # Initialize database
    conn = init_db(args.target_dir)

    try:
        # Handle knockout state reset
        if args.reset_knockout:
            clear_knockout_state(conn)
            print("Knockout state has been reset.\n")
            if not args.knockout:
                # User just wanted to reset, not start a new knockout
                print("Use -k/--knockout to start a new knockout tournament.")
                conn.close()
                sys.exit(0)

        # Initialize eliminated set
        if args.knockout:
            # Load existing knockout state from database
            eliminated = load_knockout_state(conn)

            if eliminated:
                # Resume existing knockout tournament
                stats = get_knockout_stats(conn, args.target_dir, args.pattern)
                print(f"Resuming knockout tournament...")
                print(f"  Total files in database: {stats['total_count']}")
                print(f"  Already eliminated: {stats['eliminated_count']}")
                print(f"  Still competing: {stats['competing_count']}")
                print()
        else:
            # Not in knockout mode, but keep variable for consistency
            eliminated = set()

        if args.knockout:
            print("Local Elo - File Ranking Tool (KNOCKOUT MODE)")
            print("Commands: A (file A wins), B (file B wins), a-/b- (win but remove winner), t (tie), o (open files), top [N] (show leaderboard), ren <old> <new> (rename file)")
            print("Note: Losers are eliminated! Last one standing wins.")
            print("Press Ctrl+C to exit\n")
        else:
            print("Local Elo - File Ranking Tool")
            print("Commands: A (file A wins), B (file B wins), t (tie), o (open files), top [N] (show leaderboard), ren <old> <new> (rename file)")
            print("Press Ctrl+C to exit\n")

        while True:
            # Sync files with database
            sync_files(conn, args.pattern, args.target_dir)

            # Get active files
            files = get_active_files(conn, args.target_dir, args.pattern)

            # In knockout mode, filter out eliminated players
            if args.knockout:
                files = [f for f in files if f[0] not in eliminated]

            if len(files) == 0:
                print("No files found matching the pattern.")
                break

            if len(files) == 1:
                if args.knockout:
                    # Winner found in knockout mode
                    winner = files[0]
                    print(f"\n{'='*60}")
                    print(f"WINNER: {winner[1]}")
                    print(f"Final Elo: {int(winner[2])}")
                    print(f"Record: {format_record(winner)}")
                    print(f"{'='*60}\n")
                    break
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
                user_input = input("Your choice (A/B/t/o/top [N]/ren <old> <new>): ").strip()

                # Check for top command
                top_n = parse_top_command(user_input)
                if top_n is not None:
                    display_leaderboard(conn, top_n, args.target_dir)
                    # Re-display the matchup
                    print(matchup_display)
                    continue

                # Check for open command
                if user_input.lower() == 'o':
                    # Construct full paths first
                    full_path_a = os.path.join(args.target_dir, path_a)
                    full_path_b = os.path.join(args.target_dir, path_b)

                    # Convert to absolute paths
                    abs_path_a = os.path.abspath(full_path_a)
                    abs_path_b = os.path.abspath(full_path_b)

                    # Check for custom startup script in target directory
                    custom_script = None
                    if sys.platform in ['darwin', 'linux'] or sys.platform.startswith('linux'):
                        # macOS and Linux use .sh
                        script_path = os.path.join(args.target_dir, 'elo_start.sh')
                        if os.path.exists(script_path):
                            custom_script = script_path
                    elif sys.platform == 'win32':
                        # Windows uses .bat
                        script_path = os.path.join(args.target_dir, 'elo_start.bat')
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
                            continue

                        subprocess.run([open_cmd, abs_path_a])
                        subprocess.run([open_cmd, abs_path_b])
                        print(f"Opened {path_a} and {path_b}")
                    continue

                # Check for rename command
                if user_input.lower().startswith('ren '):
                    # Parse rename command: "ren <old> <new>"
                    parts = user_input.split(maxsplit=2)
                    if len(parts) != 3:
                        print("Usage: ren <old_filename> <new_filename>")
                        continue

                    old_name = parts[1]
                    new_name = parts[2]

                    # Build full paths
                    old_path = os.path.join(args.target_dir, old_name)
                    new_path = os.path.join(args.target_dir, new_name)

                    # Validate old file exists
                    if not os.path.exists(old_path):
                        print(f"Error: File '{old_name}' not found")
                        continue

                    # Check if new file already exists
                    if os.path.exists(new_path):
                        print(f"Error: File '{new_name}' already exists")
                        continue

                    # Rename in filesystem
                    try:
                        os.rename(old_path, new_path)
                    except OSError as e:
                        print(f"Error renaming file: {e}")
                        continue

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
                    files = sync_files(conn, args.pattern, args.target_dir)
                    continue

                # Check for a-/b- commands (knockout mode only)
                if user_input.upper() in ['A-', 'B-'] and not args.knockout:
                    print("Error: a-/b- commands only available in knockout mode")
                    continue

                # Validate input
                if user_input.upper() in ['A', 'B', 'T', 'A-', 'B-']:
                    result = user_input.upper()
                    if result == 'T':
                        result = 'tie'

                    # Get rankings before the game
                    old_rankings = get_rankings(conn)

                    # Record the game (normalize A-/B- to A/B for Elo calculation)
                    game_result = result.rstrip('-') if result in ['A-', 'B-'] else result
                    record_game(conn, id_a, id_b, elo_a, elo_b, game_result)

                    # Display ranking changes
                    display_ranking_changes(conn, old_rankings, id_a, id_b, args.target_dir)

                    # In knockout mode, eliminate the loser (or winner if a-/b- used) and persist to database
                    if args.knockout:
                        remove_winner = result in ['A-', 'B-']

                        if result in ['A', 'A-']:
                            if remove_winner:
                                eliminated.add(id_a)
                                save_elimination(conn, id_a)
                                print(f"  {path_a} wins but is REMOVED from tournament!\n")
                            else:
                                eliminated.add(id_b)
                                save_elimination(conn, id_b)
                                print(f"  {path_b} has been ELIMINATED!\n")
                        elif result in ['B', 'B-']:
                            if remove_winner:
                                eliminated.add(id_b)
                                save_elimination(conn, id_b)
                                print(f"  {path_b} wins but is REMOVED from tournament!\n")
                            else:
                                eliminated.add(id_a)
                                save_elimination(conn, id_a)
                                print(f"  {path_a} has been ELIMINATED!\n")
                        # In case of tie, no one is eliminated
                        else:
                            print("  Tie - no one eliminated.\n")

                        # Show remaining players count
                        remaining_count = len([f for f in get_active_files(conn, args.target_dir, args.pattern) if f[0] not in eliminated])
                        print(f"Players remaining: {remaining_count}\n")

                    break
                else:
                    print("Invalid input. Please enter A, B, =, or top [N]")

    except KeyboardInterrupt:
        print("\n\nGoodbye!")
    finally:
        conn.close()


if __name__ == '__main__':
    main()