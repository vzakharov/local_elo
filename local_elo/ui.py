import sqlite3
import os
import re
from typing import List, Optional, Sequence, Tuple

from .constants import DEFAULT_LEADERBOARD_SIZE
from .db import get_rankings, get_knockout_results
from .colors import (
    green, red, yellow, cyan, dim, bold, bold_cyan, bold_red,
    prob_color, histogram_bar, tag_color
)
from .utils import display_name


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
            display_path = display_name(path)

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
            display_path = display_name(path)

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
                           file_ids: Sequence[int], target_dir: str = '.') -> None:
    """Display ranking changes for the files that just competed."""
    if not file_ids:
        return

    cursor = conn.cursor()

    # Get new rankings
    new_rankings = get_rankings(conn)

    placeholders = ",".join(["?"] * len(file_ids))
    cursor.execute(f'SELECT id, path, elo FROM files WHERE id IN ({placeholders})', tuple(file_ids))
    files = cursor.fetchall()

    # Create a dict for easy lookup
    files_dict = {file_id: (path, new_elo) for file_id, path, new_elo in files}

    print(f"\n{bold('Rankings:')}")
    for file_id in file_ids:
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

        display_path = display_name(path)
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
        print(f"Commands: winner slots {bold('abc')} / {bold('abc+')} / {bold('abc-')} / {bold('ac!e!')}, "
              f"{bold('t')} (tie+all pass), {bold('o')} (open shown files), "
              f"{bold('top')} [N], {bold('ren')} <old> <new>, {bold('rem')} <slots>, "
              f"{bold('tag')} <letter> <±tags> / {bold('tag ren')} <old> <new> / {bold('tag rem')} <tag>, "
              f"{bold('add')} <name>, {bold('reset')}")
        print(dim("Legacy aliases for 2-player rounds are still accepted (A/B/T and +/- variants)."))
        print(yellow("Note: Losers are eliminated! Last one standing wins."))
        print(dim("Press Ctrl+C to exit\n"))
    else:
        print(f"{bold_cyan('Local Elo')} - File Ranking Tool")
        print(f"Commands: winner slots {bold('abc')} / {bold('abc+')} / {bold('abc-')}, "
              f"{bold('t')} (all tie), {bold('o')} (open shown files), "
              f"{bold('top')} [N], {bold('ren')} <old> <new>, {bold('rem')} <slots>, "
              f"{bold('tag')} <letter> <±tags> / {bold('tag ren')} <old> <new> / {bold('tag rem')} <tag>")
        print(dim("Legacy aliases for 2-player rounds are still accepted (A/B/T and +/- variants)."))
        print(dim("Press Ctrl+C to exit\n"))


def format_competition(competitors: List[Tuple[str, str, float, int, str, List[str]]],
                       tag_colors: dict = None) -> str:
    """Format a multiplayer competition block with slot letters.

    Each competitor tuple is (slot, display_path, elo, rank, record, tags),
    where tags is a list of strings displayed under the competitor when present.

    ``tag_colors`` maps a tag to its palette index (see db.get_tag_color_map),
    giving every tag a stable color across the session. Tags absent from the map
    fall back to dimmed text.
    """
    if not competitors:
        return ""

    tag_colors = tag_colors or {}

    def render_tag(tag):
        idx = tag_colors.get(tag)
        if idx is None:
            return dim(f"#{tag}")
        return tag_color(idx, f"#{tag}")

    strongest_slot = max(competitors, key=lambda c: c[2])[0]
    lines = [bold("Competition:")]
    for slot, display_path, elo, rank, record, tags in competitors:
        name = bold(display_path) if slot == strongest_slot else display_path
        lines.append(f"  {bold(slot)}: {name} ({int(elo)} / #{rank} / {record})")
        if tags:
            tag_str = " ".join(render_tag(tag) for tag in tags)
            lines.append(f"     {tag_str}")

    lines.append(dim("Command: slots for winners (e.g. ac), 't' for tie-all, '+' all pass, '-' winners do not pass"))
    return "\n".join(lines)
