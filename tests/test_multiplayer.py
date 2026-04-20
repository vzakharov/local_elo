import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_elo.db import add_file_to_db, init_db, load_round_played
from local_elo.elo import calculate_multiplayer_elo_deltas, record_competition, update_elo_ratings
from local_elo.knockout import handle_game_result
from local_elo.outcome import MatchOutcome, parse_outcome_command


class MultiplayerParserTests(unittest.TestCase):
    def test_compact_command_with_suffix(self):
        outcome = parse_outcome_command("acd+", 5)
        self.assertEqual(outcome.winner_slots, {0, 2, 3})
        self.assertEqual(outcome.pass_slots, {0, 1, 2, 3, 4})
        self.assertFalse(outcome.tie_all)

    def test_tie_command(self):
        outcome = parse_outcome_command("t", 4)
        self.assertEqual(outcome.winner_slots, {0, 1, 2, 3})
        self.assertEqual(outcome.pass_slots, {0, 1, 2, 3})
        self.assertTrue(outcome.tie_all)

    def test_legacy_alias_for_two_players(self):
        outcome = parse_outcome_command("TA-", 2)
        self.assertTrue(outcome.tie_all)
        self.assertEqual(outcome.pass_slots, {1})

    def test_invalid_duplicate_slot(self):
        with self.assertRaises(ValueError):
            parse_outcome_command("aac", 4)


class MultiplayerEloTests(unittest.TestCase):
    def test_multiplayer_zero_sum(self):
        elos = [1100.0, 1020.0, 980.0, 950.0]
        outcome = MatchOutcome(winner_slots={0, 1}, pass_slots={0, 1}, tie_all=False, raw_command="ab")
        deltas = calculate_multiplayer_elo_deltas(elos, outcome)
        self.assertAlmostEqual(sum(deltas), 0.0, places=9)

    def test_two_player_parity_with_legacy_formula(self):
        elo_a = 1200.0
        elo_b = 1000.0
        legacy_new_a, legacy_new_b = update_elo_ratings(None, 1, 2, elo_a, elo_b, "A")

        outcome = MatchOutcome(winner_slots={0}, pass_slots={0}, tie_all=False, raw_command="a")
        deltas = calculate_multiplayer_elo_deltas([elo_a, elo_b], outcome)
        self.assertAlmostEqual(legacy_new_a, elo_a + deltas[0], places=9)
        self.assertAlmostEqual(legacy_new_b, elo_b + deltas[1], places=9)


class KnockoutPassFlowTests(unittest.TestCase):
    def test_minus_suffix_removes_winners(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["one.txt", "two.txt", "three.txt"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["one.txt", "two.txt", "three.txt"]:
                add_file_to_db(conn, name)

            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, path, elo, wins, losses, ties FROM files WHERE path IN ('one.txt', 'two.txt', 'three.txt') ORDER BY path"
            )
            competitors = cursor.fetchall()

            outcome = MatchOutcome(
                winner_slots={0, 1},
                pass_slots={2},
                tie_all=False,
                raw_command="ab-",
            )
            eliminated = set()
            tournament_pool = set()

            with patch("local_elo.elo.sys.platform", "linux"), patch("local_elo.knockout.sys.platform", "linux"):
                handle_game_result(
                    conn=conn,
                    outcome=outcome,
                    competitors=competitors,
                    target_dir=tmp_dir,
                    knockout_mode=True,
                    eliminated=eliminated,
                    pattern=".*",
                    tournament_pool=tournament_pool,
                )

            expected_eliminated = {competitors[0][0], competitors[1][0]}
            self.assertEqual(eliminated, expected_eliminated)
            self.assertEqual(load_round_played(conn), {competitors[2][0]})


if __name__ == "__main__":
    unittest.main()
