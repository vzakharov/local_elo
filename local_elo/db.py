import sqlite3
import os
import re
import csv
from datetime import datetime
from typing import List, Tuple

from . import DEFAULT_ELO, DB_NAME
from .files import discover_files


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


def remove_entry_from_database(conn: sqlite3.Connection, file_id: int) -> None:
    """
    Remove entry and all related records.
    Order matters due to foreign key constraints.
    """
    cursor = conn.cursor()

    cursor.execute('DELETE FROM knockout_state WHERE file_id = ?', (file_id,))
    cursor.execute('DELETE FROM games WHERE file_a_id = ? OR file_b_id = ?',
                   (file_id, file_id))
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


def export_knockout_results(conn: sqlite3.Connection, target_dir: str) -> str:
    """
    Export knockout tournament results to CSV.
    Returns the path to the created CSV file.

    This should only be called when the tournament has naturally completed
    (exactly 1 uneliminated player remains).
    """
    cursor = conn.cursor()

    # Query all files sorted by elimination order (winner first, then latest eliminations)
    cursor.execute('''
        SELECT f.path, f.elo, f.wins, f.losses, f.ties, k.eliminated_at
        FROM files f
        LEFT JOIN knockout_state k ON f.id = k.file_id
        ORDER BY
            CASE WHEN k.eliminated_at IS NULL THEN 0 ELSE 1 END,
            k.eliminated_at DESC,
            f.elo DESC
    ''')
    results = cursor.fetchall()

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
