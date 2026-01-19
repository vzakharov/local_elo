import sqlite3
from typing import Tuple

from .constants import K_FACTOR, DEFAULT_ELO


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


def redistribute_elo_delta(conn: sqlite3.Connection, delta: float, skip_file_id: int) -> None:
    """
    Redistribute delta uniformly across remaining entries.
    This preserves all pairwise win probabilities since rating gaps stay unchanged.

    Args:
        conn: Database connection
        delta: Amount to redistribute (removed_elo - 1000)
        skip_file_id: ID of removed entry (exclude from redistribution)
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
