"""Local Elo - A CLI tool for ranking files using Elo ratings through pairwise comparisons."""

from .constants import DEFAULT_ELO, K_FACTOR, DEFAULT_LEADERBOARD_SIZE, DB_NAME

# Public API exports
from .commands import main
from .db import init_db, get_rankings
from .files import sync_files
from .elo import calculate_win_probability, record_game
from .game import select_first_player, select_second_player
from .ui import display_leaderboard

__all__ = [
    'DEFAULT_ELO',
    'K_FACTOR',
    'DEFAULT_LEADERBOARD_SIZE',
    'DB_NAME',
    'main',
    'init_db',
    'sync_files',
    'get_rankings',
    'calculate_win_probability',
    'record_game',
    'select_first_player',
    'select_second_player',
    'display_leaderboard',
]
