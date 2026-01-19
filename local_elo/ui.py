import sqlite3
import os
import re
from typing import Tuple

from . import DEFAULT_LEADERBOARD_SIZE
from .db import get_rankings, get_knockout_results


def format_record_values(wins: int, losses: int, ties: int) -> str:
    """Format W/T/L record from individual values."""
    return f"{wins}W-{losses}L-{ties}T"


def create_elo_histogram(elo: float, max_elo: float, bar_width: int = 80) -> str:
    """
    Create a histogram bar using filled block characters.

    Args:
        elo: Current Elo rating
        max_elo: Maximum Elo rating to use as reference for scaling
        bar_width: Maximum number of blocks to display

    Returns:
        A string containing the histogram bar (filled blocks)
    """
    if max_elo <= 0:
        return ' ' * bar_width

    ratio = min(elo / max_elo, 1.0)
    filled_blocks = int(ratio * bar_width)

    # Use Unicode full block character (U+2588)
    # Pad with spaces to maintain alignment
    bar = '█' * filled_blocks
    return bar.ljust(bar_width)


def format_record(player: tuple) -> str:
    """
    Format a W/T/L record string from a player tuple.

    Args:
        player: Tuple in format (id, path, elo, wins, losses, ties)

    Returns:
        Formatted string like "12W-8L-2T"
    """
    return format_record_values(player[3], player[4], player[5])


def display_leaderboard(
    conn: sqlite3.Connection,
    limit: int = DEFAULT_LEADERBOARD_SIZE,
    target_dir: str = '.',
    sort_by: str = 'elo',
    show_all_files: bool = False,
    pattern: str = '.*'
) -> None:
    """
    Display the top N files with histogram visualization.

    Args:
        conn: Database connection
        limit: Maximum number of files to display
        target_dir: Target directory for file paths
        sort_by: Sorting mode - 'elo' (default) or 'knockout'
        show_all_files: If True, show all DB files regardless of pattern/filesystem
        pattern: Regex pattern to filter files (ignored if show_all_files=True)
    """
    if sort_by == 'knockout':
        # Get results filtered by pool (if pool exists)
        all_results = get_knockout_results(conn)

        # Filter results if needed
        if not show_all_files:
            # Filter to files that exist on disk and match pattern
            regex = re.compile(pattern)
            results = [
                r for r in all_results
                if os.path.exists(os.path.join(target_dir, r[0])) and regex.search(r[0])
            ]
        else:
            results = all_results

        # Limit results
        results = results[:limit]

        if not results:
            print(f"\nKnockout Tournament Results:\nNo files found.\n")
            return

        # Find max Elo for scaling (use first entry which is the winner)
        max_elo = results[0][1] if results else 1000

        print(f"\nKnockout Tournament Results:")
        for i, (path, elo, wins, losses, ties, eliminated_at) in enumerate(results, 1):
            # Display full path if not in current directory
            display_path = os.path.join(target_dir, path) if target_dir != '.' else path

            # Generate histogram (comes FIRST to ensure alignment)
            histogram = create_elo_histogram(elo, max_elo)

            # Format record string
            record = format_record_values(wins, losses, ties)

            # Print: histogram | rank | elo | record | path
            print(f"{histogram} {i:2d}. {int(elo):4d} ({record:12s}) {display_path}")
        print()
    else:
        # Original elo-based sorting
        cursor = conn.cursor()
        cursor.execute(
            'SELECT path, elo, wins, losses, ties FROM files ORDER BY elo DESC LIMIT ?',
            (limit,)
        )
        results = cursor.fetchall()

        if not results:
            print(f"\nTop {limit} Files:\nNo files found.\n")
            return

        # Find max Elo for scaling the histogram
        max_elo = results[0][1]

        print(f"\nTop {limit} Files:")
        for i, (path, elo, wins, losses, ties) in enumerate(results, 1):
            # Display full path if not in current directory
            display_path = os.path.join(target_dir, path) if target_dir != '.' else path

            # Generate histogram (comes FIRST to ensure alignment)
            histogram = create_elo_histogram(elo, max_elo)

            # Format record string
            record = format_record_values(wins, losses, ties)

            # Print: histogram | rank | elo | record | path
            print(f"{histogram} {i:2d}. {int(elo):4d} ({record:12s}) {display_path}")
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
