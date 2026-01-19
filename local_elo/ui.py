import sqlite3
import os
import re
from typing import Tuple, Optional

from .constants import DEFAULT_LEADERBOARD_SIZE
from .db import get_rankings, get_knockout_results
from .colors import (
    green, red, yellow, cyan, dim, bold, bold_cyan, bold_red,
    prob_color, histogram_bar
)
from .utils import get_filename


def format_record_values(wins: int, losses: int, ties: int) -> str:
    """Format W/T/L record from individual values."""
    return f"{wins}W-{losses}L-{ties}T"


def create_elo_histogram(elo: float, max_elo: float, bar_width: int = 80) -> str:
    """
    Create a colored histogram bar using filled block characters.

    Args:
        elo: Current Elo rating
        max_elo: Maximum Elo rating to use as reference for scaling
        bar_width: Maximum number of blocks to display

    Returns:
        A string containing the colored histogram bar (filled blocks)
    """
    if max_elo <= 0:
        return ' ' * bar_width

    ratio = min(elo / max_elo, 1.0)
    filled_blocks = int(ratio * bar_width)

    # Use Unicode full block character (U+2588)
    bar = '█' * filled_blocks
    colored_bar = histogram_bar(bar, ratio)
    # Pad with spaces (no color needed for padding)
    return colored_bar + ' ' * (bar_width - filled_blocks)


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
    pattern: str = '.*',
    tournament_pool: set = None
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
            print(f"\n{bold_cyan('Knockout Tournament Results:')}\nNo files found.\n")
            return

        # Find max Elo for scaling (use first entry which is the winner)
        max_elo = results[0][1] if results else 1000

        print(f"\n{bold_cyan('Knockout Tournament Results:')}")
        for i, (path, elo, wins, losses, ties, eliminated_at) in enumerate(results, 1):
            display_path = get_filename(path)

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
            print(f"\n{bold_cyan(f'Top {limit} Files:')}\nNo files found.\n")
            return

        # Find max Elo for scaling the histogram
        max_elo = results[0][1]

        # Build elimination status map for pool checking
        elimination_status = {}
        if tournament_pool:
            cursor_pool = conn.cursor()
            for path, _, _, _, _ in results:
                cursor_pool.execute('SELECT id FROM files WHERE path = ?', (path,))
                row = cursor_pool.fetchone()
                if row:
                    file_id = row[0]
                    if file_id in tournament_pool:
                        # Check if eliminated
                        cursor_pool.execute('SELECT eliminated_at FROM knockout_state WHERE file_id = ?', (file_id,))
                        elim_row = cursor_pool.fetchone()
                        elimination_status[path] = elim_row[0] if elim_row else None

        print(f"\n{bold_cyan(f'Top {limit} Files:')}")
        for i, (path, elo, wins, losses, ties) in enumerate(results, 1):
            display_path = get_filename(path)

            # Generate histogram (comes FIRST to ensure alignment)
            histogram = create_elo_histogram(elo, max_elo)

            # Format record string
            record = format_record_values(wins, losses, ties)

            # Check if file is in tournament pool
            pool_marker = ''
            if path in elimination_status:
                eliminated_at = elimination_status[path]
                # Star for still competing, circle for eliminated
                if eliminated_at is None:
                    pool_marker = f" {yellow('★')}"
                else:
                    pool_marker = f" {yellow('●')}"

            # Print: histogram | rank | elo | record | path
            print(f"{histogram} {i:2d}. {int(elo):4d} ({record:12s}){pool_marker} {display_path}")
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

    # Create a dict for easy lookup
    files_dict = {file_id: (path, new_elo) for file_id, path, new_elo in files}

    print(f"\n{bold('Rankings:')}")
    # Display in order: A first, then B
    for file_id in [file_a_id, file_b_id]:
        if file_id not in files_dict:
            continue

        path, new_elo = files_dict[file_id]
        old_rank = old_rankings.get(file_id, "N/A")
        new_rank = new_rankings.get(file_id, "N/A")

        if old_rank == new_rank:
            movement = dim(f"#{new_rank} (no change)")
        elif old_rank == "N/A":
            movement = cyan(f"#{new_rank} (new)")
        elif new_rank == "N/A":
            movement = red(f"unranked (was #{old_rank})")
        elif old_rank > new_rank:
            movement = green(f"#{new_rank} (up from #{old_rank})")
        else:
            movement = red(f"#{new_rank} (down from #{old_rank})")

        display_path = get_filename(path)
        print(f"  {cyan(display_path)}: {movement} | New Elo: {bold(str(int(new_elo)))}")
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


def display_welcome_message(knockout_mode: bool) -> None:
    """Display welcome message and available commands."""
    if knockout_mode:
        print(f"{bold_cyan('Local Elo')} - File Ranking Tool {bold_red('(KNOCKOUT MODE)')}")
        print(f"Commands: {bold('A/B')} (winner), {bold('a-/b-')} (win but remove winner), "
              f"{bold('a+/b+')} (win but loser stays), {bold('t')} (tie), "
              f"{bold('ta-/tb-/t-')} (tie but eliminate), {bold('o')} (open), "
              f"{bold('top')} [N], {bold('ren')} <old> <new>, {bold('rem')} a/b/ab")
        print(yellow("Note: Losers are eliminated! Last one standing wins."))
        print(dim("Press Ctrl+C to exit\n"))
    else:
        print(f"{bold_cyan('Local Elo')} - File Ranking Tool")
        print(f"Commands: {bold('A/B')} (winner), {bold('t')} (tie), {bold('o')} (open), "
              f"{bold('top')} [N], {bold('ren')} <old> <new>, {bold('rem')} a/b/ab")
        print(dim("Press Ctrl+C to exit\n"))


def format_matchup(display_path_a: str, elo_a: float, rank_a, record_a: str,
                   display_path_b: str, elo_b: float, rank_b, record_b: str,
                   win_prob_display: str, prob_a: float = 0.5) -> str:
    """Format matchup display string with colors."""
    # Color the favored player's path
    if prob_a >= 0.5:
        path_a_colored = bold(display_path_a)
        path_b_colored = display_path_b
    else:
        path_a_colored = display_path_a
        path_b_colored = bold(display_path_b)

    # Color win probability based on how lopsided the match is
    max_prob = max(prob_a, 1 - prob_a)
    prob_colored = prob_color(max_prob, win_prob_display)

    return (f"{bold('A')}: {path_a_colored} ({int(elo_a)} / #{rank_a} / {record_a})\n"
            f"{dim('vs')}\n"
            f"{bold('B')}: {path_b_colored} ({int(elo_b)} / #{rank_b} / {record_b})\n"
            f"Win probability: {prob_colored}")
