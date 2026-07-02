import sqlite3
import os
import re
import csv
from datetime import datetime
from typing import List, Tuple

from .constants import DEFAULT_ELO, DB_NAME


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

    # Create multiplayer match tables
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            outcome TEXT NOT NULL,
            tie_all INTEGER NOT NULL DEFAULT 0,
            command TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS match_players (
            match_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            slot_index INTEGER NOT NULL,
            is_winner INTEGER NOT NULL DEFAULT 0,
            did_pass INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (match_id, file_id),
            FOREIGN KEY (match_id) REFERENCES matches(id),
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    ''')

    # Create file_tags table (free-form tags applied to a file/player)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS file_tags (
            file_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (file_id, tag),
            FOREIGN KEY (file_id) REFERENCES files(id)
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

    # Create knockout_pool table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knockout_pool (
            file_id INTEGER PRIMARY KEY,
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    ''')

    # Create knockout_round_played table (tracks who already played this round)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knockout_round_played (
            file_id INTEGER PRIMARY KEY,
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    ''')

    # Create knockout_locked table (tracks players locked out until unlock conditions)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knockout_locked (
            file_id INTEGER PRIMARY KEY,
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
    ''')

    # Create knockout_round_number table (single-row, tracks current round)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knockout_round_number (
            round INTEGER NOT NULL DEFAULT 1,
            total INTEGER
        )
    ''')

    # Migrate: add total column if missing (existing DBs)
    cursor.execute('PRAGMA table_info(knockout_round_number)')
    columns = {row[1] for row in cursor.fetchall()}
    if 'total' not in columns:
        cursor.execute('ALTER TABLE knockout_round_number ADD COLUMN total INTEGER')

    conn.commit()
    return conn


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


def add_tags(conn: sqlite3.Connection, file_id: int, tags: List[str]) -> List[str]:
    """Apply tags to a file, returning the list of tags that were newly added.

    Re-applying an existing tag is a no-op (idempotent via the composite
    primary key). Tag text is stored exactly as provided (case-preserving).
    """
    cursor = conn.cursor()
    added = []
    for tag in tags:
        cursor.execute(
            'INSERT OR IGNORE INTO file_tags (file_id, tag) VALUES (?, ?)',
            (file_id, tag)
        )
        if cursor.rowcount > 0:
            added.append(tag)
    conn.commit()
    return added


def get_tags(conn: sqlite3.Connection, file_id: int) -> List[str]:
    """Return a file's tags, ordered by when they were applied."""
    cursor = conn.cursor()
    cursor.execute(
        'SELECT tag FROM file_tags WHERE file_id = ? ORDER BY timestamp, tag',
        (file_id,)
    )
    return [row[0] for row in cursor.fetchall()]


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


def remove_elimination(conn: sqlite3.Connection, file_id: int) -> bool:
    """Remove a file's elimination record from the database.
    Returns True if a record was removed, False if the file wasn't eliminated."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_state WHERE file_id = ?', (file_id,))
    conn.commit()
    return cursor.rowcount > 0


def clear_knockout_state(conn: sqlite3.Connection) -> None:
    """Clear all knockout state from database."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_state')
    conn.commit()


def save_knockout_pool(conn: sqlite3.Connection, file_ids: set) -> None:
    """Save the tournament pool to database."""
    cursor = conn.cursor()
    for file_id in file_ids:
        cursor.execute('INSERT OR IGNORE INTO knockout_pool (file_id) VALUES (?)', (file_id,))
    conn.commit()


def load_knockout_pool(conn: sqlite3.Connection) -> set:
    """Load tournament pool file IDs from database."""
    cursor = conn.cursor()
    cursor.execute('SELECT file_id FROM knockout_pool')
    pool_ids = {row[0] for row in cursor.fetchall()}
    return pool_ids


def clear_knockout_pool(conn: sqlite3.Connection) -> None:
    """Clear the tournament pool table."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_pool')
    conn.commit()


def save_round_played(conn: sqlite3.Connection, file_id: int) -> None:
    """Mark a player as having played this round."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO knockout_round_played (file_id) VALUES (?)',
            (file_id,)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass


def load_round_played(conn: sqlite3.Connection) -> set:
    """Load IDs of players who have already played this round."""
    cursor = conn.cursor()
    cursor.execute('SELECT file_id FROM knockout_round_played')
    return {row[0] for row in cursor.fetchall()}


def clear_round_played(conn: sqlite3.Connection) -> None:
    """Clear round-played tracking (start a new round)."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_round_played')
    conn.commit()


def save_locked(conn: sqlite3.Connection, file_id: int) -> None:
    """Mark a player as locked."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO knockout_locked (file_id) VALUES (?)',
            (file_id,)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass


def load_locked(conn: sqlite3.Connection) -> set:
    """Load IDs of players who are currently locked."""
    cursor = conn.cursor()
    cursor.execute('SELECT file_id FROM knockout_locked')
    return {row[0] for row in cursor.fetchall()}


def clear_locked(conn: sqlite3.Connection) -> None:
    """Clear all lock state."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_locked')
    conn.commit()


def clear_locked_subset(conn: sqlite3.Connection, file_ids: set) -> None:
    """Clear lock state for a specific set of file IDs."""
    if not file_ids:
        return
    cursor = conn.cursor()
    cursor.executemany(
        'DELETE FROM knockout_locked WHERE file_id = ?',
        [(file_id,) for file_id in file_ids]
    )
    conn.commit()


def get_round_info(conn: sqlite3.Connection) -> tuple:
    """Get current knockout round number and total players. Returns (round, total)."""
    cursor = conn.cursor()
    cursor.execute('SELECT round, total FROM knockout_round_number LIMIT 1')
    row = cursor.fetchone()
    return (row[0], row[1]) if row else (1, None)


def set_round_info(conn: sqlite3.Connection, round_num: int, total: int) -> None:
    """Set round number and total players for this round."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_round_number')
    cursor.execute('INSERT INTO knockout_round_number (round, total) VALUES (?, ?)', (round_num, total))
    conn.commit()


def reset_round_number(conn: sqlite3.Connection) -> None:
    """Reset round number (clears the table)."""
    cursor = conn.cursor()
    cursor.execute('DELETE FROM knockout_round_number')
    conn.commit()


def remove_entry_from_database(conn: sqlite3.Connection, file_id: int) -> None:
    """
    Remove entry and all related records.
    Order matters due to foreign key constraints.
    """
    cursor = conn.cursor()

    cursor.execute('DELETE FROM knockout_state WHERE file_id = ?', (file_id,))
    cursor.execute('DELETE FROM knockout_pool WHERE file_id = ?', (file_id,))
    cursor.execute('DELETE FROM knockout_round_played WHERE file_id = ?', (file_id,))
    cursor.execute('DELETE FROM knockout_locked WHERE file_id = ?', (file_id,))
    cursor.execute('DELETE FROM file_tags WHERE file_id = ?', (file_id,))
    cursor.execute('DELETE FROM games WHERE file_a_id = ? OR file_b_id = ?',
                   (file_id, file_id))
    cursor.execute('SELECT DISTINCT match_id FROM match_players WHERE file_id = ?', (file_id,))
    match_ids = [row[0] for row in cursor.fetchall()]
    cursor.execute('DELETE FROM match_players WHERE file_id = ?', (file_id,))
    for match_id in match_ids:
        cursor.execute('SELECT 1 FROM match_players WHERE match_id = ? LIMIT 1', (match_id,))
        if cursor.fetchone() is None:
            cursor.execute('DELETE FROM matches WHERE id = ?', (match_id,))
    cursor.execute('DELETE FROM files WHERE id = ?', (file_id,))

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


def get_rankings(conn: sqlite3.Connection) -> dict:
    """Get current rankings as a dictionary mapping file_id to rank position."""
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM files ORDER BY elo DESC')
    results = cursor.fetchall()

    rankings = {}
    for rank, (file_id,) in enumerate(results, 1):
        rankings[file_id] = rank

    return rankings


def get_knockout_results(conn: sqlite3.Connection) -> list:
    """
    Get knockout tournament results, filtered by pool if one exists.
    Returns list of (path, elo, wins, losses, ties, eliminated_at) tuples,
    sorted by elimination order (winner first, then latest eliminations).
    """
    cursor = conn.cursor()

    # Check if pool exists
    cursor.execute('SELECT COUNT(*) FROM knockout_pool')
    pool_exists = cursor.fetchone()[0] > 0

    # Conditionally add pool filter
    pool_join = 'INNER JOIN knockout_pool p ON f.id = p.file_id' if pool_exists else ''

    cursor.execute(f'''
        SELECT f.path, f.elo, f.wins, f.losses, f.ties, k.eliminated_at
        FROM files f
        {pool_join}
        LEFT JOIN knockout_state k ON f.id = k.file_id
        ORDER BY
            CASE WHEN k.eliminated_at IS NULL THEN 0 ELSE 1 END,
            k.eliminated_at DESC,
            f.elo DESC
    ''')
    return cursor.fetchall()


def export_knockout_results(conn: sqlite3.Connection, target_dir: str) -> str:
    """
    Export knockout tournament results to CSV.
    Returns the path to the created CSV file.

    This should only be called when the tournament has naturally completed
    (exactly 1 uneliminated player remains).
    """
    results = get_knockout_results(conn)

    # Generate CSV filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = f'knockout_results_{timestamp}.csv'
    csv_path = os.path.join(target_dir, csv_filename)

    # Write CSV file
    with open(csv_path, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)

        # Write header
        writer.writerow(['Position', 'Path', 'Elo', 'Record', 'Eliminated At'])

        # Write data rows
        for position, (path, elo, wins, losses, ties, eliminated_at) in enumerate(results, 1):
            # Format record as W-L-T
            record = f"{wins}W-{losses}L-{ties}T"

            # Format elimination timestamp
            if eliminated_at is None:
                elim_display = "Winner"
            else:
                # Display the elimination timestamp
                elim_display = eliminated_at

            writer.writerow([position, path, int(elo), record, elim_display])

    return csv_path
