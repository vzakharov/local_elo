import string
from dataclasses import dataclass
from typing import Optional, Set


@dataclass(frozen=True)
class MatchOutcome:
    """Normalized outcome for a multiplayer competition command."""
    winner_slots: Set[int]
    pass_slots: Set[int]
    tie_all: bool
    raw_command: str


def slot_letters(count: int) -> str:
    """Return available slot letters for a displayed competition."""
    if count < 1:
        return ""
    if count > len(string.ascii_lowercase):
        raise ValueError("Maximum supported competition size is 26 players")
    return string.ascii_lowercase[:count]


def _parse_compact_outcome(command: str, player_count: int) -> MatchOutcome:
    compact = command.strip().lower()
    if not compact:
        raise ValueError("Empty command")

    if compact == "t":
        all_slots = set(range(player_count))
        return MatchOutcome(
            winner_slots=all_slots,
            pass_slots=all_slots,
            tie_all=True,
            raw_command=command,
        )

    suffix = ""
    if compact.endswith("+") or compact.endswith("-"):
        suffix = compact[-1]
        compact = compact[:-1]

    if not compact:
        raise ValueError("Winner set cannot be empty")

    letters = slot_letters(player_count)
    slots = set()
    for char in compact:
        if char not in letters:
            raise ValueError(f"Unknown player slot '{char}'")
        if char in slots:
            raise ValueError(f"Duplicate slot '{char}'")
        slots.add(char)

    winner_slots = {letters.index(char) for char in slots}
    all_slots = set(range(player_count))
    if suffix == "+":
        pass_slots = all_slots
    elif suffix == "-":
        pass_slots = all_slots - winner_slots
    else:
        pass_slots = set(winner_slots)

    return MatchOutcome(
        winner_slots=winner_slots,
        pass_slots=pass_slots,
        tie_all=False,
        raw_command=command,
    )


def _parse_legacy_two_player(command: str) -> Optional[MatchOutcome]:
    legacy = command.strip().upper()
    if legacy not in {"A", "B", "T", "A+", "B+", "A-", "B-", "TA-", "TB-", "T-"}:
        return None

    both = {0, 1}
    if legacy == "A":
        return MatchOutcome({0}, {0}, False, command)
    if legacy == "B":
        return MatchOutcome({1}, {1}, False, command)
    if legacy == "T":
        return MatchOutcome(both, both, True, command)
    if legacy == "A+":
        return MatchOutcome({0}, both, False, command)
    if legacy == "B+":
        return MatchOutcome({1}, both, False, command)
    if legacy == "A-":
        return MatchOutcome({0}, {1}, False, command)
    if legacy == "B-":
        return MatchOutcome({1}, {0}, False, command)
    if legacy == "TA-":
        return MatchOutcome(both, {1}, True, command)
    if legacy == "TB-":
        return MatchOutcome(both, {0}, True, command)
    return MatchOutcome(both, set(), True, command)


def parse_outcome_command(command: str, player_count: int) -> MatchOutcome:
    """Parse a competition command into a normalized outcome model."""
    if player_count < 2:
        raise ValueError("At least 2 players are required")

    if player_count == 2:
        legacy = _parse_legacy_two_player(command)
        if legacy is not None:
            return legacy

    return _parse_compact_outcome(command, player_count)
