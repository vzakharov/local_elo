import random
from typing import List, Tuple

from .constants import DEFAULT_ELO
from .elo import calculate_win_probability


def select_first_player(files: List[Tuple[int, str, float, int, int, int]],
                        power: Tuple[float, float]) -> Tuple[int, str, float, int, int, int]:
    """
    Select the first player using weighted random selection.
    Combines two weights:
    1. Elo-based weight: (probability of beating an average opponent)^elo_power
    2. Games-played weight: 1 / (games_played + 1)^games_power

    power: Tuple of (games_power, elo_power)
    """
    games_power, elo_power = power
    weights = []
    for f in files:
        base_elo_weight = calculate_win_probability(f[2], DEFAULT_ELO)
        elo_weight = base_elo_weight ** elo_power

        games_played = f[3] + f[4] + f[5]
        games_weight = 1.0 / ((games_played + 1) ** games_power)

        combined_weight = elo_weight * games_weight
        weights.append(combined_weight)

    return random.choices(files, weights=weights, k=1)[0]


def select_second_player(files: List[Tuple[int, str, float, int, int, int]],
                        first_player: Tuple[int, str, float, int, int, int]) -> Tuple[int, str, float, int, int, int]:
    """
    Select the second player using weighted random selection.
    Weight = probability of weaker player beating stronger player (encourages close matches).
    """
    # Remove first player from candidates
    candidates = [f for f in files if f[0] != first_player[0]]

    if not candidates:
        return None

    # Calculate weights based on match closeness
    weights = []
    for candidate in candidates:
        # Determine who is weaker/stronger
        if first_player[2] > candidate[2]:
            # First player is stronger
            weight = calculate_win_probability(candidate[2], first_player[2])
        else:
            # Candidate is stronger
            weight = calculate_win_probability(first_player[2], candidate[2])
        weights.append(weight)

    return random.choices(candidates, weights=weights, k=1)[0]
