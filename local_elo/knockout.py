import sqlite3
import sys
import random
from typing import List, Optional, Tuple, NamedTuple

from .constants import DEFAULT_ELO
from .elo import _update_finder_comments_for_ids

TOP_SKEWING_POWER = 2.0


class PoolConfig(NamedTuple):
    """Configuration for tournament pool selection.

    total_size: Total number of players in pool (X)
    top_skewing_size: Number selected via top-skewing weighted sampling (Y)
    Remaining (X - Y) slots filled by custom weighted sampling (uses power param).
    """
    total_size: int
    top_skewing_size: int = 0

    @property
    def custom_weighted_size(self) -> int:
        """Number of slots filled by custom weighted sampling (respects power param)."""
        return self.total_size - self.top_skewing_size
from .db import (
    load_knockout_state, save_elimination, clear_knockout_state,
    get_knockout_stats, export_knockout_results, save_knockout_pool,
    load_knockout_pool, clear_knockout_pool, clear_round_played,
    save_round_played, reset_round_number, get_active_files, get_rankings,
    load_locked, save_locked, clear_locked, clear_locked_subset
)
from .elo import calculate_win_probability, record_competition
from .outcome import MatchOutcome
from .ui import display_leaderboard, display_ranking_changes
from .colors import bold, bold_red, bold_green, bold_cyan, green, red, yellow, cyan, dim
from .utils import display_name


def handle_all_locked_unlock(conn: sqlite3.Connection, active_ids: set, locked: set) -> bool:
    """
    Unlock current lock tier when every active player is locked.
    Returns True when an unlock cycle event occurred.
    """
    if not active_ids:
        return False

    active_locked = active_ids & locked
    if active_locked != active_ids:
        return False

    clear_locked_subset(conn, active_ids)
    locked.difference_update(active_ids)
    print(dim("All active players are locked — unlocking this tier and allowing locks again."))
    return True


def effective_locked(active_ids: set, locked: set, match_size: int) -> set:
    """
    Compute which active players should be excluded by lock state.
    Locks are ignored when unlocked players cannot fill the minimum required matchup size.
    """
    active_locked = active_ids & locked
    if not active_locked:
        return set()

    min_required = min(match_size, len(active_ids))
    unlocked_active_count = len(active_ids) - len(active_locked)
    if unlocked_active_count < min_required:
        return set()

    return active_locked


def handle_lock_command(conn: sqlite3.Connection, arg: str,
                        competitors: List[Tuple[int, str]], locked: set) -> bool:
    """
    Lock competitor(s) by slot letter sequence, e.g. 'a', 'ace'.
    Returns True to signal need for new matchup.
    """
    arg = arg.lower()
    valid_slots = {chr(ord('a') + idx): (file_id, path) for idx, (file_id, path) in enumerate(competitors)}
    if not arg or any(ch not in valid_slots for ch in arg):
        printable = "".join(valid_slots.keys())
        print(red(f"  Invalid lock argument: '{arg}'. Use one or more of: {printable}"))
        return False

    to_lock = []
    seen = set()
    for ch in arg:
        if ch in seen:
            continue
        seen.add(ch)
        to_lock.append(valid_slots[ch])

    locked_now = []
    already_locked = []
    for file_id, file_path in to_lock:
        disp = display_name(file_path)
        if file_id in locked:
            already_locked.append(disp)
            continue
        save_locked(conn, file_id)
        locked.add(file_id)
        locked_now.append(disp)

    if locked_now:
        print(f"{green('!')} Locked: {cyan(', '.join(locked_now))}")
    if already_locked:
        print(dim(f"  Already locked: {', '.join(already_locked)}"))

    return bool(locked_now)


def handle_game_result(conn: sqlite3.Connection, outcome: MatchOutcome,
                       competitors: List[Tuple[int, str, float, int, int, int]],
                       target_dir: str, knockout_mode: bool, eliminated: set,
                       pattern: str, tournament_pool: set) -> None:
    """
    Handle multiplayer game outcome.
    Records the game, updates rankings, and handles knockout eliminations.
    """
    old_rankings = get_rankings(conn)

    participants = [(player[0], player[2]) for player in competitors]
    record_competition(conn, participants, outcome, target_dir)
    ordered_ids = [player[0] for player in competitors]
    display_ranking_changes(conn, old_rankings, ordered_ids, target_dir)

    if not knockout_mode:
        return

    file_ids = [player[0] for player in competitors]
    path_by_slot = {idx: display_name(player[1]) for idx, player in enumerate(competitors)}
    pass_ids = {file_ids[idx] for idx in outcome.pass_slots}
    eliminated_now = [file_id for file_id in file_ids if file_id not in pass_ids]

    for file_id in eliminated_now:
        eliminated.add(file_id)
        save_elimination(conn, file_id)

    for idx, file_id in enumerate(file_ids):
        if file_id in pass_ids and file_id not in eliminated:
            save_round_played(conn, file_id)

    winners_display = [path_by_slot[idx] for idx in sorted(outcome.winner_slots)]
    pass_display = [path_by_slot[idx] for idx in sorted(outcome.pass_slots)]
    eliminated_display = [
        path_by_slot[idx] for idx, file_id in enumerate(file_ids) if file_id in eliminated_now
    ]

    if outcome.tie_all:
        print(f"  {bold('Tie')}: all players are tied.")
    else:
        print(f"  Winners: {bold_green(', '.join(winners_display))}")

    if pass_display:
        print(f"  Pass to next round: {green(', '.join(pass_display))}")
    else:
        print(f"  Pass to next round: {dim('(none)')}")

    if eliminated_display:
        print(f"  Eliminated: {bold_red(', '.join(eliminated_display))}\n")
    else:
        print(dim("  Eliminated: none\n"))

    if tournament_pool:
        remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                               if f[0] in tournament_pool and f[0] not in eliminated])
    else:
        remaining_count = len([f for f in get_active_files(conn, target_dir, pattern)
                               if f[0] not in eliminated])
    print(f"Players remaining: {bold(str(remaining_count))}\n")


def handle_reset_command(conn: sqlite3.Connection, eliminated: set, tournament_pool: set, locked: set) -> bool:
    """
    Handle the 'reset' command in knockout mode.
    Returns True if should break out of input loop to re-sync.
    """
    confirm = input("Are you sure you want to reset the knockout tournament? All eliminations will be cleared. (y/N): ").strip().lower()
    if confirm == 'y' or confirm == 'yes':
        clear_knockout_state(conn)
        clear_knockout_pool(conn)
        clear_round_played(conn)
        clear_locked(conn)
        reset_round_number(conn)
        eliminated.clear()
        tournament_pool.clear()
        locked.clear()
        print(green("Knockout tournament has been reset! All players are back in.\n"))
        return True
    else:
        print(dim("Reset cancelled.\n"))
        return False


def initialize_knockout_tournament(conn: sqlite3.Connection, target_dir: str, pattern: str,
                                    pool_config: Optional[PoolConfig], power: Tuple[float, float]) -> Tuple[set, set, set]:
    """
    Initialize or resume a knockout tournament.
    Returns (eliminated, tournament_pool, locked) sets.
    """
    eliminated = load_knockout_state(conn)
    tournament_pool = load_knockout_pool(conn)
    locked = load_locked(conn)

    if eliminated or tournament_pool:
        if pool_config:
            pool_count = len(tournament_pool) if tournament_pool else None
            if pool_count and pool_count != pool_config.total_size:
                # Format pool config for display
                if pool_config.top_skewing_size == 0:
                    config_str = str(pool_config.total_size)
                else:
                    config_str = f"{pool_config.total_size}/{pool_config.top_skewing_size}"
                
                print(red(f"ERROR: Existing knockout tournament has pool size {pool_count}, but you specified -n {config_str}"))
                print(red("Please use the same pool size or reset with --reset-knockout"))
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

            # Phase 1: Custom weighted-select (X-Y) candidates (uses power param)
            if pool_config.custom_weighted_size > 0:
                games_power, elo_power = power
                pool_weights = []
                for f in all_files:
                    base_elo_weight = calculate_win_probability(f[2], DEFAULT_ELO)
                    elo_weight = base_elo_weight ** elo_power
                    games_played = f[3] + f[4] + f[5]
                    games_weight = 1.0 / ((games_played + 1) ** games_power)
                    pool_weights.append(elo_weight * games_weight)

                # Sample (X-Y) candidates without replacement
                custom_weighted_selected = []
                remaining_files = list(all_files)
                remaining_weights = list(pool_weights)

                for _ in range(pool_config.custom_weighted_size):
                    chosen = random.choices(remaining_files, weights=remaining_weights, k=1)[0]
                    idx = remaining_files.index(chosen)
                    custom_weighted_selected.append(chosen)
                    selected_ids.add(chosen[0])
                    remaining_files.pop(idx)
                    remaining_weights.pop(idx)

                selected_files.extend(custom_weighted_selected)
                print(cyan(f"Selected {pool_config.custom_weighted_size} candidates via custom weighted sampling"))

            # Phase 2: Top-skewing weighted-select Y candidates from remaining pool
            if pool_config.top_skewing_size > 0:
                # Get candidates not already selected
                remaining_candidates = [f for f in all_files if f[0] not in selected_ids]

                # Sort by Elo (highest first) to get position-based ranking
                sorted_candidates = sorted(remaining_candidates, key=lambda f: f[2], reverse=True)
                total_remaining = len(sorted_candidates)

                # Calculate weights based on position (top positions get higher weights)
                # Position 0 (top) gets highest weight, position N-1 (bottom) gets lowest
                top_skewing_weights = []
                for idx, f in enumerate(sorted_candidates):
                    # Higher position (lower index) = higher weight
                    # Weight = (total_remaining - position) ** TOP_SKEWING_POWER
                    position_weight = (total_remaining - idx) ** TOP_SKEWING_POWER
                    top_skewing_weights.append(position_weight)

                # Use sorted candidates for selection
                remaining_candidates = sorted_candidates

                # Sample Y candidates without replacement
                top_skewing_selected = []
                for _ in range(pool_config.top_skewing_size):
                    chosen = random.choices(remaining_candidates, weights=top_skewing_weights, k=1)[0]
                    idx = remaining_candidates.index(chosen)
                    top_skewing_selected.append(chosen)
                    remaining_candidates.pop(idx)
                    top_skewing_weights.pop(idx)

                selected_files.extend(top_skewing_selected)
                print(cyan(f"Selected {pool_config.top_skewing_size} candidates via top-skewing weighted sampling"))

            tournament_pool = {f[0] for f in selected_files}
            save_knockout_pool(conn, tournament_pool)

            # Summary message
            parts = []
            if pool_config.custom_weighted_size > 0:
                parts.append(f"{pool_config.custom_weighted_size} custom weighted")
            if pool_config.top_skewing_size > 0:
                parts.append(f"{pool_config.top_skewing_size} top-skewing")

            if parts:
                breakdown = " + ".join(parts)
                print(cyan(f"Tournament pool: {pool_config.total_size} players ({breakdown})"))
            else:
                print(cyan(f"Selected {pool_config.total_size} competitors for knockout tournament"))
            print()
        else:
            tournament_pool = set()

    # Update Finder comments for all active files at tournament start/resume
    all_active_ids = [f[0] for f in get_active_files(conn, target_dir, pattern)]
    _update_finder_comments_for_ids(conn, target_dir, all_active_ids)

    return eliminated, tournament_pool, locked


def handle_winner_screen(conn: sqlite3.Connection, target_dir: str, pattern: str,
                         eliminated: set, tournament_pool: set, locked: set) -> bool:
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
            clear_round_played(conn)
            clear_locked(conn)
            reset_round_number(conn)
            eliminated.clear()
            tournament_pool.clear()
            locked.clear()
            print(green("Knockout tournament reset! All players are back in.\n"))
            break
        elif user_input in ['q', 'quit']:
            should_exit = True
            break
        else:
            print(yellow("Invalid input. Please type 'reset' or 'q'.\n"))

    return should_exit
