import os
import re
import fnmatch
import datetime
import sys
import subprocess
import sqlite3
from typing import List, Tuple, Optional

from . import DB_NAME
from .db import sync_files, remove_entry_from_database
from .elo import redistribute_elo_delta


def discover_files(pattern: str, target_dir: str = '.') -> List[str]:
    """
    Discover files in the target directory matching the regex pattern.
    Excludes the script itself, the database file, and hidden/system files.
    """
    files = []
    print(f"Discovering files in {target_dir} with pattern {pattern}")
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


def trash_file(filepath: str, target_dir: str) -> None:
    """Move file to .trash subdirectory with timestamp."""
    if not os.path.exists(filepath):
        print(f"Warning: File {filepath} does not exist on disk")
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
        print(f"Moved to trash: {trash_path}")
    except OSError as e:
        print(f"Warning: Could not trash file: {e}")


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


def handle_rem_command(conn: sqlite3.Connection, arg: str, id_a: int, id_b: int,
                       path_a: str, path_b: str, target_dir: str,
                       files: List[Tuple], eliminated: set, tournament_pool: set) -> bool:
    """
    Remove competitor(s) by reference: 'a', 'b', or 'ab'.
    Returns True to signal need for new matchup.
    """
    arg = arg.lower()
    if arg not in ('a', 'b', 'ab', 'ba'):
        print(f"  Invalid argument: '{arg}'. Use 'a', 'b', or 'ab'.")
        return False

    to_remove = []
    if 'a' in arg:
        to_remove.append((id_a, path_a))
    if 'b' in arg:
        to_remove.append((id_b, path_b))

    for file_id, file_path in to_remove:
        cursor = conn.cursor()
        cursor.execute("SELECT elo FROM files WHERE id = ?", (file_id,))
        row = cursor.fetchone()
        if not row:
            continue

        file_elo = row[0]
        delta = file_elo - 1000

        full_path = os.path.join(target_dir, file_path) if target_dir != '.' else file_path
        redistribute_elo_delta(conn, delta, file_id)
        trash_file(full_path, target_dir)
        remove_entry_from_database(conn, file_id)

        eliminated.discard(file_id)
        tournament_pool.discard(file_id)

        print(f"✓ Removed {file_path} and redistributed {delta:+.1f} Elo")

    return True


def handle_open_command(path_a: str, path_b: str, target_dir: str) -> None:
    """Handle the 'o' command to open both files."""
    full_path_a = os.path.join(target_dir, path_a)
    full_path_b = os.path.join(target_dir, path_b)

    abs_path_a = os.path.abspath(full_path_a)
    abs_path_b = os.path.abspath(full_path_b)

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
            subprocess.run(['bash', custom_script, abs_path_a])
            subprocess.run(['bash', custom_script, abs_path_b])
        else:
            subprocess.run([custom_script, abs_path_a])
            subprocess.run([custom_script, abs_path_b])
        print(f"Opened {path_a} and {path_b} using {os.path.basename(custom_script)}")
    else:
        if sys.platform == 'darwin':
            open_cmd = 'open'
        elif sys.platform.startswith('linux'):
            open_cmd = 'xdg-open'
        elif sys.platform == 'win32':
            open_cmd = 'start'
        else:
            print("Unsupported platform for opening files")
            return

        subprocess.run([open_cmd, abs_path_a])
        subprocess.run([open_cmd, abs_path_b])
        print(f"Opened {path_a} and {path_b}")


def handle_rename_command(conn: sqlite3.Connection, user_input: str, target_dir: str,
                          pattern: str, path_a: str, path_b: str) -> Tuple[str, str]:
    """
    Handle the 'ren' command to rename files.
    Returns updated (path_a, path_b) in case one was renamed.
    """
    parts = user_input.split(maxsplit=2)
    if len(parts) != 3:
        print("Usage: ren <old_filename> <new_filename>")
        return path_a, path_b

    old_name = parts[1]
    new_name = parts[2]

    if '*' in old_name:
        try:
            matches = apply_wildcard_rename(old_name, new_name, target_dir)
            
            conflict_found = False
            for old_filename, new_filename in matches:
                new_path = os.path.join(target_dir, new_filename)
                if os.path.exists(new_path):
                    print(f"Error: File '{new_filename}' already exists")
                    conflict_found = True
                    break
            
            if conflict_found:
                return path_a, path_b
            
            cursor = conn.cursor()
            renamed_count = 0
            for old_filename, new_filename in matches:
                old_path = os.path.join(target_dir, old_filename)
                new_path = os.path.join(target_dir, new_filename)
                
                try:
                    os.rename(old_path, new_path)
                    cursor.execute('UPDATE files SET path = ? WHERE path = ?', (new_filename, old_filename))
                    renamed_count += 1
                    
                    if path_a == old_filename:
                        path_a = new_filename
                    if path_b == old_filename:
                        path_b = new_filename
                except OSError as e:
                    print(f"Error renaming '{old_filename}' to '{new_filename}': {e}")
            
            conn.commit()
            print(f"Renamed {renamed_count} file(s)")
            
        except ValueError as e:
            print(f"Error: {e}")
        
        sync_files(conn, pattern, target_dir)
        return path_a, path_b
    else:
        old_path = os.path.join(target_dir, old_name)
        new_path = os.path.join(target_dir, new_name)

        if not os.path.exists(old_path):
            print(f"Error: File '{old_name}' not found")
            return path_a, path_b

        if os.path.exists(new_path):
            print(f"Error: File '{new_name}' already exists")
            return path_a, path_b

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            print(f"Error renaming file: {e}")
            return path_a, path_b

        cursor = conn.cursor()
        cursor.execute('UPDATE files SET path = ? WHERE path = ?', (new_name, old_name))
        conn.commit()

        print(f"Renamed '{old_name}' to '{new_name}'")

        if path_a == old_name:
            path_a = new_name
        if path_b == old_name:
            path_b = new_name

        sync_files(conn, pattern, target_dir)
        return path_a, path_b
