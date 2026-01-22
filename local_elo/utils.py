"""Utility functions for local_elo."""

import os
import re

# Global configuration for link pattern
LINK_PATTERN = None


def _format_hyperlink(text: str, url: str, enabled: bool = True) -> str:
    """
    Format text as a terminal hyperlink using OSC 8 escape sequences.

    Args:
        text: Display text
        url: Target URL
        enabled: Whether hyperlinks are enabled

    Returns:
        Text with OSC 8 hyperlink formatting, or plain text if not enabled

    Examples:
        _format_hyperlink("hello", "https://example.com/hello")
        -> "\033]8;;https://example.com/hello\033\\hello\033]8;;\033\\"
    """
    if not enabled or not url:
        return text

    # OSC 8 format: ESC ]8;;URL ST text ESC ]8;; ST
    # ST (String Terminator) = ESC \
    OSC = '\033]'
    ST = '\033\\'

    return f"{OSC}8;;{url}{ST}{text}{OSC}8;;{ST}"


def _build_link_url(filename: str, pattern: str) -> str:
    """
    Build URL from filename and pattern by replacing * wildcard.

    Args:
        filename: Base filename without extension
        pattern: URL pattern with * placeholder

    Returns:
        Complete URL with https:// prefix

    Examples:
        _build_link_url("john-doe", "linkedin.com/in/*")
        -> "https://linkedin.com/in/john-doe"

        _build_link_url("photo", "https://example.com/*/view")
        -> "https://example.com/photo/view"
    """
    if not pattern or '*' not in pattern:
        return ""

    # Replace wildcard with filename
    url = pattern.replace('*', filename)

    # Add https:// prefix if not present
    if not url.startswith(('http://', 'https://')):
        url = f"https://{url}"

    return url


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


def display_name(filepath: str) -> str:
    """
    Format filename with optional hyperlink for display.

    Args:
        filepath: Path to file (may include directory)

    Returns:
        Formatted filename (with hyperlink if LINK_PATTERN is set)

    Examples:
        With LINK_PATTERN="linkedin.com/in/*":
            display_name("dir/hello.png") -> hyperlinked "hello"

        With LINK_PATTERN=None:
            display_name("dir/hello.png") -> plain "hello"
    """
    filename = get_filename(filepath)

    if not LINK_PATTERN:
        return filename

    url = _build_link_url(filename, LINK_PATTERN)

    if not url:
        return filename

    # Import here to avoid circular dependency
    from .colors import COLORS_ENABLED

    # Only enable hyperlinks if colors are enabled (same terminal capability check)
    return _format_hyperlink(filename, url, enabled=COLORS_ENABLED)


def extensions_to_pattern(extensions: str) -> str:
    """
    Convert comma-separated extensions to a regex pattern.

    Args:
        extensions: Comma-separated list (e.g., "py,js,ts" or ".py,.js,.ts")

    Returns:
        Regex pattern that matches any of the extensions

    Examples:
        "py,js" -> r'.*\\.(py|js)$'
        ".py,.js" -> r'.*\\.(py|js)$'
        "py" -> r'.*\\.py$'
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
