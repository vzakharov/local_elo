"""Utility functions for local_elo."""

import os
import re


def get_filename(filepath: str) -> str:
    """
    Extract just the base filename without directory or extension.

    Args:
        filepath: Path to file (may include directory)

    Returns:
        Base filename without extension

    Examples:
        "dir/file.py" -> "file"
        "file.py" -> "file"
        "file.tar.gz" -> "file.tar"
        "file" -> "file"
    """
    filename = os.path.basename(filepath)
    name_without_ext, _ = os.path.splitext(filename)
    return name_without_ext


def extensions_to_pattern(extensions: str) -> str:
    """
    Convert comma-separated extensions to a regex pattern.

    Args:
        extensions: Comma-separated list (e.g., "py,js,ts" or ".py,.js,.ts")

    Returns:
        Regex pattern that matches any of the extensions

    Examples:
        "py,js" -> r'.*\.(py|js)$'
        ".py,.js" -> r'.*\.(py|js)$'
        "py" -> r'.*\.py$'
    """
    # Split, strip whitespace, remove leading dots
    ext_list = [e.strip().lstrip('.') for e in extensions.split(',')]

    # Filter empty strings
    ext_list = [e for e in ext_list if e]

    if not ext_list:
        return '.*'  # Match all files if empty

    # Escape special regex characters
    ext_list = [re.escape(e) for e in ext_list]

    # Build pattern
    if len(ext_list) == 1:
        return rf'.*\.{ext_list[0]}$'
    else:
        return rf'.*\.({"|".join(ext_list)})$'
