import os
import re
import fnmatch
import datetime
import sys
import subprocess
import sqlite3
from typing import List, Tuple, Optional

from .constants import DB_NAME
from .db import add_file_to_db, remove_entry_from_database, remove_elimination
from .elo import redistribute_elo_delta
from .colors import green, red, yellow, cyan, bold, dim
from .utils import get_filename, display_name


def discover_files(pattern: str, target_dir: str = '.') -> List[str]:
    """
    Discover files in the target directory matching the regex pattern.
    Excludes the script itself, the database file, and hidden/system files.
    """
    files = []
    print(dim(f"Discovering files in {target_dir} with pattern {pattern}"))
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


def sync_files(conn: sqlite3.Connection, pattern: str, target_dir: str = '.') -> None:
    """Sync discovered files with the database."""
    files = discover_files(pattern, target_dir)
    for filepath in files:
        add_file_to_db(conn, filepath)


def trash_file(filepath: str, target_dir: str) -> None:
    """Move file to .trash subdirectory with timestamp."""
    if not os.path.exists(filepath):
        print(yellow(f"Warning: File {filepath} does not exist on disk"))
        return

    trash_dir = os.path.join(target_dir, '.trash')
    os.makedirs(trash_dir, exist_ok=True)

    basename = os.path.basename(filepath)
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    name, ext = os.path.splitext(basename)
    trash_name = f"{name}_{timestamp}{ext}"
    trash_path = os.path.join(trash_dir, trash_name)

    try:
        os.rename(filepath, trash_path)
        print(f"Moved to trash: {dim(trash_path)}")
    except OSError as e:
        print(yellow(f"Warning: Could not trash file: {e}"))


def apply_wildcard_rename(old_pattern: str, new_pattern: str, target_dir: str) -> List[Tuple[str, str]]:
    """
    Apply wildcard rename pattern to matching files.
    
    Args:
        old_pattern: Pattern with * wildcard (e.g., "hello_*")
        new_pattern: Replacement pattern with * wildcard (e.g., "hey_*")
        target_dir: Directory to search for files
        
    Returns:
        List of (old_filename, new_filename) tuples for matched files
        
    Raises:
        ValueError: If pattern has multiple * characters or no matches found
    """
    if old_pattern.count('*') != 1:
        raise ValueError("Pattern must contain exactly one * wildcard")
    
    if new_pattern.count('*') != 1:
        raise ValueError("Replacement pattern must contain exactly one * wildcard")
    
    matches = []
    old_prefix, old_suffix = old_pattern.split('*', 1)
    
    for filename in os.listdir(target_dir):
        if os.path.isdir(os.path.join(target_dir, filename)):
            continue
        
        if fnmatch.fnmatch(filename, old_pattern):
            matched_part = filename[len(old_prefix):len(filename) - len(old_suffix)]
            new_filename = new_pattern.replace('*', matched_part)
            matches.append((filename, new_filename))
    
    if not matches:
        raise ValueError(f"No files found matching pattern '{old_pattern}'")
    
    return matches


def handle_rem_command(conn: sqlite3.Connection, arg: str, competitors: List[Tuple[int, str]],
                       target_dir: str, files: List[Tuple], eliminated: set,
                       tournament_pool: set) -> bool:
    """
    Remove competitor(s) by slot letter sequence, e.g. 'a', 'bd'.
    Returns True to signal need for new matchup.
    """
    arg = arg.lower()
    valid_slots = {chr(ord('a') + idx): (file_id, path) for idx, (file_id, path) in enumerate(competitors)}
    if not arg or any(ch not in valid_slots for ch in arg):
        printable = "".join(valid_slots.keys())
        print(red(f"  Invalid argument: '{arg}'. Use one or more of: {printable}"))
        return False

    to_remove = []
    seen = set()
    for ch in arg:
        if ch in seen:
            continue
        seen.add(ch)
        to_remove.append(valid_slots[ch])

    for file_id, file_path in to_remove:
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        if not row:
            continue

        file_elo = row[0]
        delta = file_elo - 1000

        full_path = os.path.join(target_dir, file_path) if target_dir != '.' else file_path
        redistribute_elo_delta(conn, delta, file_id, target_dir)
        trash_file(full_path, target_dir)
        remove_entry_from_database(conn, file_id)

        eliminated.discard(file_id)
        tournament_pool.discard(file_id)

        print(f"{green('✓')} Removed {cyan(file_path)} and redistributed {bold(f'{delta:+.1f}')} Elo")

    return True


def handle_refresh_command(conn: sqlite3.Connection, target_dir: str = '.',
                           eliminated: set = None, tournament_pool: set = None,
                           locked: set = None) -> set:
    """
    Delete DB entries whose file no longer exists on disk, then recalculate
    remaining Elos once so total = (remaining N) x 1000.

    The recalculation (which spawns the CPU-intensive Finder-comment update) is
    deliberately batched into a single pass at the end rather than run per
    removed entry. Files that exist on disk but are missing from the DB are NOT
    handled here — they are added routinely by sync_files during normal play.

    The current regex pattern is intentionally ignored: a file that exists but
    doesn't match the active filter is still on disk and must not be removed.

    Returns the set of removed file IDs.
    """
    cursor = conn.cursor()
    cursor.execute('SELECT id, path, elo FROM files')
    all_rows = cursor.fetchall()

    stale = [
        (file_id, path, elo)
        for file_id, path, elo in all_rows
        if not os.path.exists(os.path.join(target_dir, path))
    ]

    if not stale:
        print(dim(f"Nothing to refresh — all {len(all_rows)} entries exist on disk."))
        return set()

    total_delta = 0.0
    removed_ids = set()
    for file_id, path, elo in stale:
        total_delta += elo - 1000
        remove_entry_from_database(conn, file_id)
        removed_ids.add(file_id)

    # Clean up in-memory tracking sets when provided (mirrors handle_rem_command).
    for tracking_set in (eliminated, tournament_pool, locked):
        if tracking_set is not None:
            tracking_set -= removed_ids

    remaining = len(all_rows) - len(removed_ids)

    # Single recalculation pass at the end (one Finder-comment update for all).
    redistribute_elo_delta(conn, total_delta, target_dir=target_dir)

    entry_word = "entry" if len(removed_ids) == 1 else "entries"
    print(
        f"{green('✓')} Removed {bold(str(len(removed_ids)))} stale {entry_word}, "
        f"redistributed {bold(f'{total_delta:+.1f}')} Elo across {remaining} file(s)"
    )

    return removed_ids


def handle_add_command(conn: sqlite3.Connection, arg: str, target_dir: str,
                       pattern: str, eliminated: set, tournament_pool: set) -> bool:
    """
    Add/restore a player to the knockout tournament by partial filename match.
    Returns True to signal need for new matchup, False otherwise.
    """
    arg = arg.strip()
    if not arg:
        print(red("  Usage: add <partial_filename>"))
        return False

    # Query all files from DB (including eliminated ones)
    cursor = conn.cursor()
    cursor.execute('SELECT id, path, elo, wins, losses, ties FROM files')
    all_db_files = cursor.fetchall()

    # Filter to files that exist and match pattern
    regex = re.compile(pattern)
    existing_files = [
        f for f in all_db_files
        if os.path.exists(os.path.join(target_dir, f[1])) and regex.search(f[1])
    ]

    # Find matches by partial filename (case-insensitive)
    arg_lower = arg.lower()
    matches = []
    for file_tuple in existing_files:
        file_id, file_path = file_tuple[0], file_tuple[1]
        filename = get_filename(file_path)
        if arg_lower in filename.lower():
            matches.append(file_tuple)

    if not matches:
        print(red(f"  No files found matching '{arg}'"))
        return False

    if len(matches) > 1:
        print(yellow(f"  Multiple matches for '{arg}':"))
        for file_tuple in matches[:10]:
            file_id, file_path = file_tuple[0], file_tuple[1]
            status = ""
            if file_id in eliminated:
                status = red(" [eliminated]")
            elif tournament_pool and file_id not in tournament_pool:
                status = dim(" [not in pool]")
            print(f"    - {display_name(file_path)}{status}")
        if len(matches) > 10:
            print(dim(f"    ... and {len(matches) - 10} more"))
        print(yellow("  Please be more specific."))
        return False

    # Single match found
    file_id, file_path = matches[0][0], matches[0][1]
    disp = display_name(file_path)

    # Case 1: Player is eliminated - restore them
    if file_id in eliminated:
        eliminated.discard(file_id)
        remove_elimination(conn, file_id)
        print(f"{green('+')} Restored {cyan(disp)} to the tournament")
        return True

    # Case 2: Tournament pool exists
    if tournament_pool:
        if file_id in tournament_pool:
            print(yellow(f"  {disp} is already active in the tournament"))
            return False
        else:
            # Not in pool - add to pool
            tournament_pool.add(file_id)
            cursor.execute('INSERT OR IGNORE INTO knockout_pool (file_id) VALUES (?)', (file_id,))
            conn.commit()
            print(f"{green('+')} Added {cyan(disp)} to the tournament pool")
            return True

    # No pool restriction - player is already active
    print(yellow(f"  {disp} is already active (not eliminated)"))
    return False


def handle_open_command(paths: List[str], target_dir: str) -> None:
    """Handle the 'o' command to open all displayed files."""
    if not paths:
        print(yellow("No files to open"))
        return

    abs_paths = [os.path.abspath(os.path.join(target_dir, path)) for path in paths]
    abs_paths.reverse()

    custom_script = None
    if sys.platform in ['darwin', 'linux'] or sys.platform.startswith('linux'):
        script_path = os.path.join(target_dir, 'elo_start.sh')
        if os.path.exists(script_path):
            custom_script = script_path
    elif sys.platform == 'win32':
        script_path = os.path.join(target_dir, 'elo_start.bat')
        if os.path.exists(script_path):
            custom_script = script_path

    if custom_script:
        if sys.platform in ['darwin', 'linux'] or sys.platform.startswith('linux'):
            for abs_path in abs_paths:
                subprocess.run(['bash', custom_script, abs_path])
        else:
            for abs_path in abs_paths:
                subprocess.run([custom_script, abs_path])
        print(f"Opened {len(paths)} file(s) using {os.path.basename(custom_script)}")
    else:
        if sys.platform == 'darwin':
            open_cmd = 'open'
        elif sys.platform.startswith('linux'):
            open_cmd = 'xdg-open'
        elif sys.platform == 'win32':
            open_cmd = 'start'
        else:
            print(yellow("Unsupported platform for opening files"))
            return

        for abs_path in abs_paths:
            subprocess.run([open_cmd, abs_path])
        print(f"Opened {len(paths)} file(s)")


def handle_rename_command(conn: sqlite3.Connection, user_input: str, target_dir: str,
                          pattern: str, current_paths: List[str]) -> List[str]:
    """
    Handle the 'ren' command to rename files.
    Returns updated current_paths in case visible files were renamed.
    """
    parts = user_input.split(maxsplit=2)
    if len(parts) != 3:
        print(yellow("Usage: ren <old_filename> <new_filename>"))
        return current_paths

    old_name = parts[1]
    new_name = parts[2]

    if '*' in old_name:
        try:
            matches = apply_wildcard_rename(old_name, new_name, target_dir)
            
            conflict_found = False
            for old_filename, new_filename in matches:
                new_path = os.path.join(target_dir, new_filename)
                if os.path.exists(new_path):
                    print(red(f"Error: File '{new_filename}' already exists"))
                    conflict_found = True
                    break
            
            if conflict_found:
                return current_paths
            
            cursor = conn.cursor()
            renamed_count = 0
            for old_filename, new_filename in matches:
                old_path = os.path.join(target_dir, old_filename)
                new_path = os.path.join(target_dir, new_filename)
                
                try:
                    os.rename(old_path, new_path)
                    cursor.execute('UPDATE files SET path = ? WHERE path = ?', (new_filename, old_filename))
                    renamed_count += 1
                    
                    current_paths = [
                        new_filename if existing == old_filename else existing
                        for existing in current_paths
                    ]
                except OSError as e:
                    print(red(f"Error renaming '{old_filename}' to '{new_filename}': {e}"))

            conn.commit()
            print(green(f"Renamed {renamed_count} file(s)"))

        except ValueError as e:
            print(red(f"Error: {e}"))
        
        sync_files(conn, pattern, target_dir)
        return current_paths
    else:
        old_path = os.path.join(target_dir, old_name)
        new_path = os.path.join(target_dir, new_name)

        if not os.path.exists(old_path):
            print(red(f"Error: File '{old_name}' not found"))
            return current_paths

        if os.path.exists(new_path):
            print(red(f"Error: File '{new_name}' already exists"))
            return current_paths

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            print(red(f"Error renaming file: {e}"))
            return current_paths

        cursor = conn.cursor()
        cursor.execute('UPDATE files SET path = ? WHERE path = ?', (new_name, old_name))
        conn.commit()

        print(green(f"Renamed '{old_name}' to '{new_name}'"))

        current_paths = [
            new_name if existing == old_name else existing
            for existing in current_paths
        ]

        sync_files(conn, pattern, target_dir)
        return current_paths
