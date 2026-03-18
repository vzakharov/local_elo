import os
import sys
import subprocess
import sqlite3
import threading
from typing import Tuple

from .constants import K_FACTOR, DEFAULT_ELO


def calculate_win_probability(elo_a: float, elo_b: float) -> float:
    """Calculate the probability of player A beating player B using Elo formula."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


def _set_finder_comment(filepath: str, comment: str) -> None:
    """Set macOS Finder comment on a file. No-op on non-macOS."""
    if sys.platform != 'darwin':
        return
    abs_path = os.path.abspath(filepath)
    script = (
        f'set posixPath to "{abs_path}"\n'
        f'set finderComment to "{comment}"\n'
        'tell application "Finder"\n'
        '  set theFile to (POSIX file posixPath) as alias\n'
        '  set comment of theFile to finderComment\n'
        'end tell'
    )
    try:
        subprocess.run(['osascript', '-e', script],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def _format_finder_comment(elo: float, wins: int, losses: int, ties: int) -> str:
    """Format the Finder comment string: EEEE (WWW/LLL/TTT)."""
    elo_str = f"{min(max(int(round(elo)), 0), 9999):04d}"
    w_str = f"{min(wins, 999):03d}"
    l_str = f"{min(losses, 999):03d}"
    t_str = f"{min(ties, 999):03d}"
    return f"{elo_str} ({w_str}/{l_str}/{t_str})"


def _update_finder_comments_for_ids(conn: sqlite3.Connection, target_dir: str,
                                     file_ids: list) -> None:
    """Update macOS Finder comments for the given file IDs (fire-and-forget)."""
    if sys.platform != 'darwin':
        return
    # Gather data on the calling thread (SQLite connections aren't thread-safe)
    updates = []
    cursor = conn.cursor()
    for file_id in file_ids:
        cursor.execute('SELECT path, elo, wins, losses, ties FROM files WHERE id = ?',
                       (file_id,))
        row = cursor.fetchone()
        if not row:
            continue
        path, elo, wins, losses, ties = row
        full_path = os.path.join(target_dir, path)
        if os.path.exists(full_path):
            updates.append((full_path, _format_finder_comment(elo, wins, losses, ties)))
    if updates:
        def _apply():
            for path, comment in updates:
                _set_finder_comment(path, comment)
        threading.Thread(target=_apply, daemon=True).start()


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


def redistribute_elo_delta(conn: sqlite3.Connection, delta: float,
                           skip_file_id: int, target_dir: str = '.') -> None:
    """
    Redistribute delta uniformly across remaining entries.
    This preserves all pairwise win probabilities since rating gaps stay unchanged.

    Args:
        conn: Database connection
        delta: Amount to redistribute (removed_elo - 1000)
        skip_file_id: ID of removed entry (exclude from redistribution)
        target_dir: Target directory for Finder comment updates
    """
    if abs(delta) < 0.01:
        return

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM files WHERE id != ?', (skip_file_id,))
    count = cursor.fetchone()[0]

    if count == 0:
        print("Warning: No remaining entries to redistribute Elo to")
        return

    adjustment = delta / count

    cursor.execute(
        'UPDATE files SET elo = elo + ? WHERE id != ?',
        (adjustment, skip_file_id)
    )

    conn.commit()

    # Update Finder comments for all affected files
    cursor.execute('SELECT id FROM files WHERE id != ?', (skip_file_id,))
    affected_ids = [row[0] for row in cursor.fetchall()]
    _update_finder_comments_for_ids(conn, target_dir, affected_ids)


def record_game(conn: sqlite3.Connection, file_a_id: int, file_b_id: int,
                elo_a: float, elo_b: float, result: str,
                target_dir: str = '.') -> None:
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

    # Update Finder comments for both players
    _update_finder_comments_for_ids(conn, target_dir, [file_a_id, file_b_id])
