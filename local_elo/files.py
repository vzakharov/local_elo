import os
import re
import fnmatch
import datetime
from typing import List, Tuple

from . import DB_NAME


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
