import sqlite3
import sys
import random
from typing import Optional, Tuple

from .constants import DEFAULT_ELO
from .db import (
    load_knockout_state, save_elimination, clear_knockout_state,
    get_knockout_stats, export_knockout_results, save_knockout_pool,
    load_knockout_pool, clear_knockout_pool, get_active_files, get_rankings
)
from .elo import calculate_win_probability, record_game
from .ui import display_leaderboard, display_ranking_changes


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
                print(f"  {path_a} wins but is REMOVED from tournament!\n")
            elif keep_loser:
                print(f"  {path_a} wins, but both players stay in tournament!\n")
            else:
                eliminated.add(id_b)
                save_elimination(conn, id_b)
                print(f"  {path_b} has been ELIMINATED!\n")
        elif result in ['B', 'B-', 'B+']:
            if remove_winner:
                eliminated.add(id_b)
                save_elimination(conn, id_b)
                print(f"  {path_b} wins but is REMOVED from tournament!\n")
            elif keep_loser:
                print(f"  {path_b} wins, but both players stay in tournament!\n")
            else:
                eliminated.add(id_a)
                save_elimination(conn, id_a)
                print(f"  {path_a} has been ELIMINATED!\n")
        elif result == 'TA-':
            eliminated.add(id_a)
            save_elimination(conn, id_a)
            print(f"  Tie, but {path_a} is REMOVED from tournament!\n")
        elif result == 'TB-':
            eliminated.add(id_b)
            save_elimination(conn, id_b)
            print(f"  Tie, but {path_b} is REMOVED from tournament!\n")
        elif result == 'T-':
            eliminated.add(id_a)
            eliminated.add(id_b)
            save_elimination(conn, id_a)
            save_elimination(conn, id_b)
            print(f"  Tie, but BOTH players are REMOVED from tournament!\n")
        else:
            print("  Tie - no one eliminated.\n")

        if tournament_pool:
            remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                                  if f[0] in tournament_pool and f[0] not in eliminated])
        else:
            remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                                  if f[0] not in eliminated])
        print(f"Players remaining: {remaining_count}\n")


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
        print("Knockout tournament has been reset! All players are back in.\n")
        return True
    else:
        print("Reset cancelled.\n")
        return False


def initialize_knockout_tournament(conn: sqlite3.Connection, target_dir: str, pattern: str,
                                    pool_size: Optional[int], power: float) -> Tuple[set, set]:
    """
    Initialize or resume a knockout tournament.
    Returns (eliminated, tournament_pool) sets.
    """
    eliminated = load_knockout_state(conn)
    tournament_pool = load_knockout_pool(conn)

    if eliminated or tournament_pool:
        if pool_size:
            pool_count = len(tournament_pool) if tournament_pool else None
            if pool_count and pool_count != pool_size:
                print(f"ERROR: Existing knockout tournament has pool size {pool_count}, but you specified -n {pool_size}")
                print("Options:")
                print("  1. Continue without -n flag to resume existing tournament")
                print("  2. Run 'reset' command to start a new tournament with new pool size")
                sys.exit(1)

        stats = get_knockout_stats(conn, target_dir, pattern)
        competing_count = len(tournament_pool) - len(eliminated) if tournament_pool else stats['competing_count']
        print(f"Resuming knockout tournament...")
        if tournament_pool:
            print(f"  Tournament pool size: {len(tournament_pool)}")
        print(f"  Total files in database: {stats['total_count']}")
        print(f"  Already eliminated: {stats['eliminated_count']}")
        print(f"  Still competing: {competing_count}")
        print()
    else:
        if pool_size:
            all_files = get_active_files(conn, target_dir, pattern)
            if len(all_files) < pool_size:
                print(f"ERROR: Only {len(all_files)} files available, but pool size is {pool_size}")
                sys.exit(1)

            pool_weights = []
            for f in all_files:
                elo_weight = calculate_win_probability(f[2], DEFAULT_ELO)
                games_played = f[3] + f[4] + f[5]
                games_weight = 1.0 / ((games_played + 1) ** power)
                pool_weights.append(elo_weight * games_weight)

            # Use weighted sampling WITHOUT replacement to ensure exactly pool_size unique entries
            selected_files = []
            remaining_files = list(all_files)
            remaining_weights = list(pool_weights)

            for _ in range(pool_size):
                chosen = random.choices(remaining_files, weights=remaining_weights, k=1)[0]
                idx = remaining_files.index(chosen)
                selected_files.append(chosen)
                remaining_files.pop(idx)
                remaining_weights.pop(idx)

            tournament_pool = {f[0] for f in selected_files}
            save_knockout_pool(conn, tournament_pool)
            print(f"Selected {pool_size} competitors for knockout tournament")
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
    print(f"\n{'='*60}")
    print("KNOCKOUT TOURNAMENT COMPLETE!")
    print(f"{'='*60}\n")

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM files')
    total_files_count = cursor.fetchone()[0]

    display_leaderboard(
        conn,
        limit=total_files_count,
        target_dir=target_dir,
        sort_by='knockout',
        show_all_files=True,
        pattern=pattern
    )

    print("Type 'reset' to start a new tournament and export results to CSV, or 'q' to quit.")

    should_exit = False
    while True:
        user_input = input("> ").strip().lower()
        if user_input == 'reset':
            csv_path = export_knockout_results(conn, target_dir)
            print(f"\nResults exported to: {csv_path}\n")

            clear_knockout_state(conn)
            clear_knockout_pool(conn)
            eliminated.clear()
            tournament_pool.clear()
            print("Knockout tournament reset! All players are back in.\n")
            break
        elif user_input in ['q', 'quit']:
            should_exit = True
            break
        else:
            print("Invalid input. Please type 'reset' or 'q'.\n")

    return should_exit
