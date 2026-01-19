"""ANSI color utilities for terminal output."""

import sys
import os


def _supports_color() -> bool:
    """Detect if the terminal supports ANSI colors."""
    # Respect NO_COLOR environment variable (https://no-color.org/)
    if os.environ.get('NO_COLOR'):
        return False
    # Respect FORCE_COLOR for CI environments
    if os.environ.get('FORCE_COLOR'):
        return True
    # Check if stdout is a TTY
    if not hasattr(sys.stdout, 'isatty') or not sys.stdout.isatty():
        return False
    # Windows needs special handling (modern Windows Terminal supports ANSI)
    if sys.platform == 'win32':
        return bool(os.environ.get('WT_SESSION') or os.environ.get('TERM'))
    return True


COLORS_ENABLED = _supports_color()


class Style:
    """ANSI style codes."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


class Fg:
    """ANSI foreground color codes."""
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    CYAN = '\033[36m'
    BRIGHT_GREEN = '\033[92m'


def _apply(code: str, text: str) -> str:
    """Apply ANSI code to text if colors are enabled."""
    if not COLORS_ENABLED:
        return text
    return f"{code}{text}{Style.RESET}"


def green(text: str) -> str:
    """Green text - for positive changes, wins, success."""
    return _apply(Fg.GREEN, text)


def red(text: str) -> str:
    """Red text - for negative changes, losses, errors."""
    return _apply(Fg.RED, text)


def yellow(text: str) -> str:
    """Yellow text - for warnings, neutral info."""
    return _apply(Fg.YELLOW, text)


def cyan(text: str) -> str:
    """Cyan text - for headers, titles, emphasis."""
    return _apply(Fg.CYAN, text)


def dim(text: str) -> str:
    """Dimmed text - for secondary info, no change."""
    return _apply(Style.DIM, text)


def bold(text: str) -> str:
    """Bold text - for emphasis, important info."""
    return _apply(Style.BOLD, text)


def bold_red(text: str) -> str:
    """Bold red - for dramatic eliminations, critical errors."""
    if not COLORS_ENABLED:
        return text
    return f"{Style.BOLD}{Fg.RED}{text}{Style.RESET}"


def bold_green(text: str) -> str:
    """Bold green - for wins, victories."""
    if not COLORS_ENABLED:
        return text
    return f"{Style.BOLD}{Fg.GREEN}{text}{Style.RESET}"


def bold_cyan(text: str) -> str:
    """Bold cyan - for headers."""
    if not COLORS_ENABLED:
        return text
    return f"{Style.BOLD}{Fg.CYAN}{text}{Style.RESET}"


def prob_color(prob: float, text: str) -> str:
    """Color based on win probability (0.0 to 1.0)."""
    if not COLORS_ENABLED:
        return text
    if prob >= 0.7:
        return green(text)
    elif prob >= 0.55:
        return yellow(text)
    else:
        return dim(text)


def histogram_bar(bar: str, ratio: float) -> str:
    """Color histogram bar based on relative position."""
    if not COLORS_ENABLED:
        return bar
    if ratio >= 0.9:
        return _apply(Fg.BRIGHT_GREEN, bar)
    elif ratio >= 0.7:
        return _apply(Fg.GREEN, bar)
    elif ratio >= 0.5:
        return _apply(Fg.CYAN, bar)
    elif ratio >= 0.3:
        return _apply(Fg.BLUE, bar)
    else:
        return dim(bar)
