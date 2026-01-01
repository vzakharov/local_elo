#!/usr/bin/env python3
"""Local Elo - A CLI tool for ranking files using Elo ratings through pairwise comparisons."""

import sqlite3
import re
import random
import sys
import os
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

    conn.commit()
    return conn


def discover_files(pattern: str, target_dir: str = '.') -> List[str]:
    """
    Discover files in the target directory matching the regex pattern.
    Excludes the script itself, the database file, and hidden/system files.
    """
    files = []
    regex = re.compile(pattern)

    for filename in os.listdir(target_dir):
        # Skip directories
        if os.path.isdir(os.path.join(target_dir, filename)):
            continue

        # Skip hidden/system files (starting with .)
        if filename.startswith('.'):
            continue

        # Skip the script itself and database
        if filename in ['local_elo.py', DB_NAME]:
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


def get_active_files(conn: sqlite3.Connection, target_dir: str = '.') -> List[Tuple[int, str, float, int, int, int]]:
    """Get all files that still exist in the filesystem."""
    cursor = conn.cursor()
    cursor.execute('SELECT id, path, elo, wins, losses, ties FROM files')
    all_files = cursor.fetchall()

    # Filter to only files that still exist
    active_files = [f for f in all_files if os.path.exists(os.path.join(target_dir, f[1]))]
    return active_files


def select_first_player(files: List[Tuple[int, str, float, int, int, int]]) -> Tuple[int, str, float, int, int, int]:
    """
    Select the first player using weighted random selection.
    Weight = probability of beating an average opponent (DEFAULT_ELO).
    """
    weights = [calculate_win_probability(f[2], DEFAULT_ELO) for f in files]
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


def display_leaderboard(conn: sqlite3.Connection, limit: int = DEFAULT_LEADERBOARD_SIZE) -> None:
    """Display the top N files by Elo rating."""
    cursor = conn.cursor()
    cursor.execute(
        'SELECT path, elo, wins, losses, ties FROM files ORDER BY elo DESC LIMIT ?',
        (limit,)
    )
    results = cursor.fetchall()

    print(f"\nTop {limit} Files:")
    for i, (path, elo, wins, losses, ties) in enumerate(results, 1):
        print(f"{i}. {int(elo)} ({wins}W-{losses}L-{ties}T) {path}")
    print()


def display_ranking_changes(conn: sqlite3.Connection, old_rankings: dict,
                           file_a_id: int, file_b_id: int) -> None:
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

        print(f"  {path}: {movement} | New Elo: {int(new_elo)}")
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
    args = parser.parse_args()

    # Initialize database
    conn = init_db(args.target_dir)

    try:
        print("Local Elo - File Ranking Tool")
        print("Commands: A (file A wins), B (file B wins), = (tie), top [N] (show leaderboard)")
        print("Press Ctrl+C to exit\n")

        while True:
            # Sync files with database
            sync_files(conn, args.pattern, args.target_dir)

            # Get active files
            files = get_active_files(conn, args.target_dir)

            if len(files) == 0:
                print("No files found matching the pattern.")
                break

            if len(files) == 1:
                print("Only one file found. Need at least two files for comparison.")
                break

            # Select two players
            first_player = select_first_player(files)
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

            print(f"A: {path_a} ({int(elo_a)} / #{rank_a}) vs B: {path_b} ({int(elo_b)} / #{rank_b})")

            # Get user input
            while True:
                user_input = input("Your choice (A/B/=/top [N]): ").strip()

                # Check for top command
                top_n = parse_top_command(user_input)
                if top_n is not None:
                    display_leaderboard(conn, top_n)
                    # Re-display the matchup
                    print(f"A: {path_a} ({int(elo_a)} / #{rank_a}) vs B: {path_b} ({int(elo_b)} / #{rank_b})")
                    continue

                # Validate input
                if user_input.upper() in ['A', 'B', '=']:
                    result = user_input.upper()
                    if result == '=':
                        result = 'tie'

                    # Get rankings before the game
                    old_rankings = get_rankings(conn)

                    # Record the game
                    record_game(conn, id_a, id_b, elo_a, elo_b, result)

                    # Display ranking changes
                    display_ranking_changes(conn, old_rankings, id_a, id_b)

                    break
                else:
                    print("Invalid input. Please enter A, B, =, or top [N]")

    except KeyboardInterrupt:
        print("\n\nGoodbye!")
    finally:
        conn.close()


if __name__ == '__main__':
    main()