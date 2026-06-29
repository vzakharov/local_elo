import os
import sys
import subprocess
import sqlite3
import threading
from typing import Dict, List, Sequence, Tuple

from .constants import K_FACTOR, DEFAULT_ELO
from .outcome import MatchOutcome


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


def calculate_multiplayer_elo_deltas(elos: Sequence[float], outcome: MatchOutcome) -> List[float]:
    """
    Calculate Elo deltas for an N-player competition.

    Uses normalized pairwise decomposition:
        delta_i = K/(N-1) * sum_j(actual_ij - expected_ij)
    """
    n_players = len(elos)
    if n_players < 2:
        raise ValueError("At least two players are required for Elo updates")

    residual_sums = [0.0] * n_players

    for i in range(n_players):
        for j in range(i + 1, n_players):
            expected_i = calculate_win_probability(elos[i], elos[j])
            expected_j = 1.0 - expected_i

            if outcome.tie_all:
                actual_i = actual_j = 0.5
            else:
                i_is_winner = i in outcome.winner_slots
                j_is_winner = j in outcome.winner_slots
                if i_is_winner == j_is_winner:
                    actual_i = actual_j = 0.5
                elif i_is_winner:
                    actual_i, actual_j = 1.0, 0.0
                else:
                    actual_i, actual_j = 0.0, 1.0

            residual_sums[i] += actual_i - expected_i
            residual_sums[j] += actual_j - expected_j

    scale = K_FACTOR / float(n_players - 1)
    return [scale * residual for residual in residual_sums]


def _outcome_label_for_storage(outcome: MatchOutcome) -> str:
    if outcome.tie_all:
        return "tie"
    return "".join(sorted(chr(ord("a") + idx) for idx in outcome.winner_slots))


def record_competition(
    conn: sqlite3.Connection,
    participants: Sequence[Tuple[int, float]],
    outcome: MatchOutcome,
    target_dir: str = ".",
) -> Dict[int, float]:
    """
    Record an N-player competition and update Elo/stats.

    participants: list of (file_id, current_elo) ordered by displayed slots.
    Returns mapping file_id -> new_elo.
    """
    if len(participants) < 2:
        raise ValueError("At least two participants are required")

    file_ids = [file_id for file_id, _ in participants]
    old_elos = [elo for _, elo in participants]
    deltas = calculate_multiplayer_elo_deltas(old_elos, outcome)
    new_elos_by_id = {
        file_id: old_elo + delta
        for (file_id, old_elo), delta in zip(participants, deltas)
    }

    winners = set(outcome.winner_slots)
    cursor = conn.cursor()
    for slot_index, file_id in enumerate(file_ids):
        new_elo = new_elos_by_id[file_id]
        if outcome.tie_all:
            cursor.execute(
                "UPDATE files SET elo = ?, ties = ties + 1 WHERE id = ?",
                (new_elo, file_id),
            )
        elif slot_index in winners:
            cursor.execute(
                "UPDATE files SET elo = ?, wins = wins + 1 WHERE id = ?",
                (new_elo, file_id),
            )
        else:
            cursor.execute(
                "UPDATE files SET elo = ?, losses = losses + 1 WHERE id = ?",
                (new_elo, file_id),
            )

    # Keep legacy games table populated for strict backward compatibility in 2-player mode.
    if len(participants) == 2:
        result = "tie"
        if not outcome.tie_all:
            if 0 in winners and 1 not in winners:
                result = "A"
            elif 1 in winners and 0 not in winners:
                result = "B"
        cursor.execute(
            "INSERT INTO games (file_a_id, file_b_id, result) VALUES (?, ?, ?)",
            (file_ids[0], file_ids[1], result),
        )

    cursor.execute(
        "INSERT INTO matches (outcome, tie_all, command) VALUES (?, ?, ?)",
        (_outcome_label_for_storage(outcome), int(outcome.tie_all), outcome.raw_command),
    )
    match_id = cursor.lastrowid
    for slot_index, file_id in enumerate(file_ids):
        cursor.execute(
            """
            INSERT INTO match_players (match_id, file_id, slot_index, is_winner, did_pass)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                match_id,
                file_id,
                slot_index,
                int(slot_index in winners),
                int(slot_index in outcome.pass_slots),
            ),
        )

    conn.commit()
    _update_finder_comments_for_ids(conn, target_dir, file_ids)
    return new_elos_by_id


def redistribute_elo_delta(conn: sqlite3.Connection, delta: float,
                           skip_file_id: int = None, target_dir: str = '.') -> None:
    """
    Redistribute delta uniformly across remaining entries.
    This preserves all pairwise win probabilities since rating gaps stay unchanged.

    Args:
        conn: Database connection
        delta: Amount to redistribute (removed_elo - 1000)
        skip_file_id: ID of removed entry to exclude from redistribution.
            Pass None to redistribute across all current entries (e.g. when the
            removed entries have already been deleted from the database).
        target_dir: Target directory for Finder comment updates
    """
    if abs(delta) < 0.01:
        return

    cursor = conn.cursor()
    if skip_file_id is None:
        cursor.execute('SELECT COUNT(*) FROM files')
    else:
        cursor.execute('SELECT COUNT(*) FROM files WHERE id != ?', (skip_file_id,))
    count = cursor.fetchone()[0]

    if count == 0:
        print("Warning: No remaining entries to redistribute Elo to")
        return

    adjustment = delta / count

    if skip_file_id is None:
        cursor.execute('UPDATE files SET elo = elo + ?', (adjustment,))
    else:
        cursor.execute(
            'UPDATE files SET elo = elo + ? WHERE id != ?',
            (adjustment, skip_file_id)
        )

    conn.commit()

    # Update Finder comments for all affected files
    if skip_file_id is None:
        cursor.execute('SELECT id FROM files')
    else:
        cursor.execute('SELECT id FROM files WHERE id != ?', (skip_file_id,))
    affected_ids = [row[0] for row in cursor.fetchall()]
    _update_finder_comments_for_ids(conn, target_dir, affected_ids)


def record_game(conn: sqlite3.Connection, file_a_id: int, file_b_id: int,
                elo_a: float, elo_b: float, result: str,
                target_dir: str = '.') -> None:
    """Backward-compatible wrapper that routes 2-player updates through multiplayer engine."""
    if result == "A":
        outcome = MatchOutcome(winner_slots={0}, pass_slots={0}, tie_all=False, raw_command="A")
    elif result == "B":
        outcome = MatchOutcome(winner_slots={1}, pass_slots={1}, tie_all=False, raw_command="B")
    else:
        outcome = MatchOutcome(winner_slots={0, 1}, pass_slots={0, 1}, tie_all=True, raw_command="T")

    record_competition(
        conn,
        participants=[(file_a_id, elo_a), (file_b_id, elo_b)],
        outcome=outcome,
        target_dir=target_dir,
    )
