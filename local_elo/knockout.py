import sqlite3
import sys
import random
from typing import Optional, Tuple, NamedTuple

from .constants import DEFAULT_ELO


class PoolConfig(NamedTuple):
    """Configuration for tournament pool selection."""
    total_size: int
    hard_selected: int = 0
    bottom_selected: int = 0

    @property
    def weighted_size(self) -> int:
        """Number of slots filled by weighted sampling."""
        return self.total_size - self.hard_selected - self.bottom_selected
from .db import (
    load_knockout_state, save_elimination, clear_knockout_state,
    get_knockout_stats, export_knockout_results, save_knockout_pool,
    load_knockout_pool, clear_knockout_pool, get_active_files, get_rankings
)
from .elo import calculate_win_probability, record_game
from .ui import display_leaderboard, display_ranking_changes
from .colors import bold, bold_red, bold_green, bold_cyan, green, red, yellow, cyan, dim
from .utils import display_name


def handle_game_result(conn: sqlite3.Connection, result: str, id_a: int, id_b: int,
                       elo_a: float, elo_b: float, path_a: str, path_b: str,
                       target_dir: str, knockout_mode: bool, eliminated: set,
                       pattern: str, tournament_pool: set) -> None:
    """
    Handle game result input (A, B, t, a-, b-, a+, b+, ta-, tb-, t-).
    Records the game, updates rankings, and handles knockout eliminations.
    """
    old_rankings = get_rankings(conn)

    if result in ['A-', 'B-', 'A+', 'B+']:
        game_result = result.rstrip('-+')
    elif result in ['TA-', 'TB-', 'T-']:
        game_result = 'tie'
    else:
        game_result = result
    record_game(conn, id_a, id_b, elo_a, elo_b, game_result)

    display_ranking_changes(conn, old_rankings, id_a, id_b, target_dir)

    if knockout_mode:
        remove_winner = result in ['A-', 'B-']
        keep_loser = result in ['A+', 'B+']

        if result in ['A', 'A-', 'A+']:
            if remove_winner:
                eliminated.add(id_a)
                save_elimination(conn, id_a)
                display_path = display_name(path_a)
                print(f"  {bold_green(display_path)} wins but is {bold_red('REMOVED')} from tournament!\n")
            elif keep_loser:
                display_path = display_name(path_a)
                print(f"  {bold_green(display_path)} wins, but both players stay in tournament!\n")
            else:
                eliminated.add(id_b)
                save_elimination(conn, id_b)
                display_path = display_name(path_a)
                print(f"  {bold_green(display_path)} {bold_green('PROCEEDS')}!\n")
        elif result in ['B', 'B-', 'B+']:
            if remove_winner:
                eliminated.add(id_b)
                save_elimination(conn, id_b)
                display_path = display_name(path_b)
                print(f"  {bold_green(display_path)} wins but is {bold_red('REMOVED')} from tournament!\n")
            elif keep_loser:
                display_path = display_name(path_b)
                print(f"  {bold_green(display_path)} wins, but both players stay in tournament!\n")
            else:
                eliminated.add(id_a)
                save_elimination(conn, id_a)
                display_path = display_name(path_b)
                print(f"  {bold_green(display_path)} {bold_green('PROCEEDS')}!\n")
        elif result == 'TA-':
            eliminated.add(id_a)
            save_elimination(conn, id_a)
            display_path = display_name(path_a)
            print(f"  Tie, but {bold_red(display_path)} is {bold_red('REMOVED')} from tournament!\n")
        elif result == 'TB-':
            eliminated.add(id_b)
            save_elimination(conn, id_b)
            display_path = display_name(path_b)
            print(f"  Tie, but {bold_red(display_path)} is {bold_red('REMOVED')} from tournament!\n")
        elif result == 'T-':
            eliminated.add(id_a)
            eliminated.add(id_b)
            save_elimination(conn, id_a)
            save_elimination(conn, id_b)
            print(f"  Tie, but {bold_red('BOTH')} players are {bold_red('REMOVED')} from tournament!\n")
        else:
            print(dim("  Tie - no one eliminated.\n"))

        if tournament_pool:
            remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                                  if f[0] in tournament_pool and f[0] not in eliminated])
        else:
            remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                                  if f[0] not in eliminated])
        print(f"Players remaining: {bold(str(remaining_count))}\n")


def handle_reset_command(conn: sqlite3.Connection, eliminated: set, tournament_pool: set) -> bool:
    """
    Handle the 'reset' command in knockout mode.
    Returns True if should break out of input loop to re-sync.
    """
    confirm = input("Are you sure you want to reset the knockout tournament? All eliminations will be cleared. (y/N): ").strip().lower()
    if confirm == 'y' or confirm == 'yes':
        clear_knockout_state(conn)
        clear_knockout_pool(conn)
        eliminated.clear()
        tournament_pool.clear()
        print(green("Knockout tournament has been reset! All players are back in.\n"))
        return True
    else:
        print(dim("Reset cancelled.\n"))
        return False


def initialize_knockout_tournament(conn: sqlite3.Connection, target_dir: str, pattern: str,
                                    pool_config: Optional[PoolConfig], power: float) -> Tuple[set, set]:
    """
    Initialize or resume a knockout tournament.
    Returns (eliminated, tournament_pool) sets.
    """
    eliminated = load_knockout_state(conn)
    tournament_pool = load_knockout_pool(conn)

    if eliminated or tournament_pool:
        if pool_config:
            pool_count = len(tournament_pool) if tournament_pool else None
            if pool_count and pool_count != pool_config.total_size:
                # Format pool config for display
                if pool_config.bottom_selected > 0:
                    if pool_config.hard_selected > 0:
                        config_str = f"{pool_config.total_size}/{pool_config.hard_selected}/{pool_config.bottom_selected}"
                    else:
                        config_str = f"{pool_config.total_size}//{pool_config.bottom_selected}"
                elif pool_config.hard_selected > 0:
                    config_str = f"{pool_config.total_size}/{pool_config.hard_selected}"
                else:
                    config_str = str(pool_config.total_size)
                
                print(red(f"ERROR: Existing knockout tournament has pool size {pool_count}, but you specified -n {config_str}"))
                print("Options:")
                print(f"  1. Continue without {bold('-n')} flag to resume existing tournament")
                print(f"  2. Run '{bold('reset')}' command to start a new tournament with new pool size")
                sys.exit(1)

        stats = get_knockout_stats(conn, target_dir, pattern)
        competing_count = len(tournament_pool) - len(eliminated) if tournament_pool else stats['competing_count']
        print(cyan("Resuming knockout tournament..."))
        if tournament_pool:
            print(f"  Tournament pool size: {bold(str(len(tournament_pool)))}")
        print(f"  Total files in database: {bold(str(stats['total_count']))}")
        print(f"  Already eliminated: {red(str(stats['eliminated_count']))}")
        print(f"  Still competing: {green(str(competing_count))}")
        print()
    else:
        if pool_config:
            all_files = get_active_files(conn, target_dir, pattern)
            if len(all_files) < pool_config.total_size:
                print(red(f"ERROR: Only {len(all_files)} files available, but pool size is {pool_config.total_size}"))
                sys.exit(1)

            selected_files = []
            selected_ids = set()

            # Phase 1: Hard-select top N players by Elo
            if pool_config.hard_selected > 0:
                sorted_by_elo = sorted(all_files, key=lambda f: f[2], reverse=True)
                hard_selected = sorted_by_elo[:pool_config.hard_selected]
                selected_files.extend(hard_selected)
                selected_ids.update(f[0] for f in hard_selected)
                print(cyan(f"Hard-selected top {pool_config.hard_selected} players by Elo rating"))

            # Phase 2: Hard-select bottom P players by Elo (from original pool)
            if pool_config.bottom_selected > 0:
                sorted_by_elo_asc = sorted(all_files, key=lambda f: f[2], reverse=False)
                bottom_selected = []
                for f in sorted_by_elo_asc:
                    if f[0] not in selected_ids:
                        bottom_selected.append(f)
                        selected_ids.add(f[0])
                        if len(bottom_selected) >= pool_config.bottom_selected:
                            break
                selected_files.extend(bottom_selected)
                print(cyan(f"Hard-selected bottom {pool_config.bottom_selected} players by Elo rating"))

            # Phase 3: Weighted-sample remaining slots
            remaining_candidates = [f for f in all_files if f[0] not in selected_ids]
            if pool_config.weighted_size > 0:
                pool_weights = []
                for f in remaining_candidates:
                    elo_weight = calculate_win_probability(f[2], DEFAULT_ELO)
                    games_played = f[3] + f[4] + f[5]
                    games_weight = 1.0 / ((games_played + 1) ** power)
                    pool_weights.append(elo_weight * games_weight)

                weighted_selected = []
                remaining_files = list(remaining_candidates)
                remaining_weights = list(pool_weights)

                for _ in range(pool_config.weighted_size):
                    chosen = random.choices(remaining_files, weights=remaining_weights, k=1)[0]
                    idx = remaining_files.index(chosen)
                    weighted_selected.append(chosen)
                    remaining_files.pop(idx)
                    remaining_weights.pop(idx)

                selected_files.extend(weighted_selected)

            tournament_pool = {f[0] for f in selected_files}
            save_knockout_pool(conn, tournament_pool)

            # Summary message
            parts = []
            if pool_config.hard_selected > 0:
                parts.append(f"{pool_config.hard_selected} top")
            if pool_config.weighted_size > 0:
                parts.append(f"{pool_config.weighted_size} sampled")
            if pool_config.bottom_selected > 0:
                parts.append(f"{pool_config.bottom_selected} bottom")

            if parts:
                breakdown = " + ".join(parts)
                print(cyan(f"Tournament pool: {pool_config.total_size} players ({breakdown})"))
            else:
                print(cyan(f"Selected {pool_config.total_size} competitors for knockout tournament"))
            print()
        else:
            tournament_pool = set()

    return eliminated, tournament_pool


def handle_winner_screen(conn: sqlite3.Connection, target_dir: str, pattern: str,
                         eliminated: set, tournament_pool: set) -> bool:
    """
    Display winner screen and handle reset/quit.
    Returns True if should exit main loop, False to continue.
    """
    print(f"\n{bold_cyan('='*60)}")
    print(bold_green("KNOCKOUT TOURNAMENT COMPLETE!"))
    print(f"{bold_cyan('='*60)}\n")

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM files')
    total_files_count = cursor.fetchone()[0]

    display_leaderboard(
        conn,
        limit=total_files_count,
        target_dir=target_dir,
        sort_by='knockout',
        show_all_files=True,
        pattern=pattern,
        tournament_pool=tournament_pool
    )

    print(f"Type '{bold('reset')}' to start a new tournament and export results to CSV, or '{bold('q')}' to quit.")

    should_exit = False
    while True:
        user_input = input("> ").strip().lower()
        if user_input == 'reset':
            csv_path = export_knockout_results(conn, target_dir)
            print(f"\n{green('Results exported to:')} {cyan(csv_path)}\n")

            clear_knockout_state(conn)
            clear_knockout_pool(conn)
            eliminated.clear()
            tournament_pool.clear()
            print(green("Knockout tournament reset! All players are back in.\n"))
            break
        elif user_input in ['q', 'quit']:
            should_exit = True
            break
        else:
            print(yellow("Invalid input. Please type 'reset' or 'q'.\n"))

    return should_exit
