import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import os

from local_elo.db import add_file_to_db, init_db, load_round_played, load_locked
from local_elo.elo import (
    calculate_multiplayer_elo_deltas, record_competition, redistribute_elo_delta,
    update_elo_ratings,
)
from local_elo.files import handle_refresh_command, handle_rename_command
from local_elo.knockout import (
    effective_locked, handle_all_locked_unlock, handle_game_result, handle_lock_command
)
from local_elo.outcome import MatchOutcome, parse_outcome_command, parse_outcome_with_lock_modifiers


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

    def test_inline_lock_modifier_locks_last_slot(self):
        outcome, locked = parse_outcome_with_lock_modifiers("ace!", 5)
        self.assertEqual(outcome.winner_slots, {0, 2, 4})
        self.assertEqual(outcome.pass_slots, {0, 2, 4})
        self.assertEqual(locked, {4})

    def test_inline_lock_modifier_locks_middle_slot(self):
        outcome, locked = parse_outcome_with_lock_modifiers("ac!e", 5)
        self.assertEqual(outcome.winner_slots, {0, 2, 4})
        self.assertEqual(outcome.pass_slots, {0, 2, 4})
        self.assertEqual(locked, {2})

    def test_inline_lock_modifier_locks_multiple_slots(self):
        outcome, locked = parse_outcome_with_lock_modifiers("ac!e!", 5)
        self.assertEqual(outcome.winner_slots, {0, 2, 4})
        self.assertEqual(outcome.pass_slots, {0, 2, 4})
        self.assertEqual(locked, {2, 4})

    def test_inline_lock_all_winners_can_be_locked(self):
        outcome, locked = parse_outcome_with_lock_modifiers("a!", 2)
        self.assertEqual(outcome.winner_slots, {0})
        self.assertEqual(outcome.pass_slots, {0})
        self.assertEqual(locked, {0})


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


class KnockoutLockFlowTests(unittest.TestCase):
    def test_lock_command_supports_multiple_slots(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]:
                add_file_to_db(conn, name)

            cursor = conn.cursor()
            cursor.execute("SELECT id, path FROM files ORDER BY path")
            competitors = cursor.fetchall()
            locked = set()

            changed = handle_lock_command(conn, "ace", competitors, locked)
            self.assertTrue(changed)

            expected_locked = {competitors[0][0], competitors[2][0], competitors[4][0]}
            self.assertEqual(locked, expected_locked)
            self.assertEqual(load_locked(conn), expected_locked)

    def test_effective_locked_excludes_when_unlocked_can_fill_match(self):
        active = {1, 2, 3, 4, 5}
        locked = {1, 3}
        self.assertEqual(effective_locked(active, locked, match_size=3), {1, 3})

    def test_effective_locked_ignored_when_unlocked_cannot_fill_match(self):
        active = set(range(1, 11))
        locked = {1, 2, 3}
        self.assertEqual(effective_locked(active, locked, match_size=10), set())

    def test_all_locked_unlock_cycle_clears_active_locks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["a.txt", "b.txt", "c.txt"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["a.txt", "b.txt", "c.txt"]:
                add_file_to_db(conn, name)

            cursor = conn.cursor()
            cursor.execute("SELECT id, path FROM files ORDER BY path")
            competitors = cursor.fetchall()
            locked = set()

            handle_lock_command(conn, "abc", competitors, locked)
            active_ids = {file_id for file_id, _ in competitors}

            unlocked = handle_all_locked_unlock(conn, active_ids, locked)
            self.assertTrue(unlocked)
            self.assertEqual(locked, set())
            self.assertEqual(load_locked(conn), set())

            relock = handle_lock_command(conn, "a", competitors, locked)
            self.assertTrue(relock)
            self.assertEqual(len(locked), 1)

    def test_lock_suffix_flow_keeps_normal_game_scoring_then_locks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["a.txt", "b.txt"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["a.txt", "b.txt"]:
                add_file_to_db(conn, name)

            cursor = conn.cursor()
            cursor.execute("SELECT id, path, elo, wins, losses, ties FROM files ORDER BY path")
            competitors = cursor.fetchall()
            a_id = competitors[0][0]
            b_id = competitors[1][0]

            eliminated = set()
            outcome = parse_outcome_command("a", 2)
            with patch("local_elo.elo.sys.platform", "linux"), patch("local_elo.knockout.sys.platform", "linux"):
                handle_game_result(
                    conn=conn,
                    outcome=outcome,
                    competitors=competitors,
                    target_dir=tmp_dir,
                    knockout_mode=True,
                    eliminated=eliminated,
                    pattern=".*",
                    tournament_pool=set(),
                )

            cursor.execute("SELECT id, elo, wins, losses, ties FROM files WHERE id IN (?, ?) ORDER BY id", (a_id, b_id))
            rows = cursor.fetchall()
            by_id = {row[0]: row[1:] for row in rows}

            self.assertGreater(by_id[a_id][0], 1000.0)
            self.assertLess(by_id[b_id][0], 1000.0)
            self.assertEqual(eliminated, {b_id})

            locked = set()
            locked_changed = handle_lock_command(conn, "a", [(a_id, "a.txt"), (b_id, "b.txt")], locked)
            self.assertTrue(locked_changed)
            self.assertEqual(locked, {a_id})


class RefreshCommandTests(unittest.TestCase):
    def _elos_by_path(self, conn):
        cursor = conn.cursor()
        cursor.execute("SELECT path, elo FROM files")
        return {row[0]: row[1] for row in cursor.fetchall()}

    def test_refresh_purges_stale_and_preserves_total(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            names = ["a.txt", "b.txt", "c.txt", "d.txt", "e.txt"]
            for name in names:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in names:
                add_file_to_db(conn, name)

            cursor = conn.cursor()
            cursor.execute("SELECT id, path, elo, wins, losses, ties FROM files ORDER BY path")
            rows = cursor.fetchall()
            by_path = {row[1]: row for row in rows}

            with patch("local_elo.elo.sys.platform", "linux"):
                # Diverge Elos with a couple of matches (total stays 5000).
                record_competition(
                    conn,
                    participants=[(by_path["a.txt"][0], 1000.0), (by_path["b.txt"][0], 1000.0)],
                    outcome=MatchOutcome(winner_slots={0}, pass_slots={0}, tie_all=False, raw_command="a"),
                    target_dir=tmp_dir,
                )
                record_competition(
                    conn,
                    participants=[(by_path["c.txt"][0], 1000.0), (by_path["d.txt"][0], 1000.0)],
                    outcome=MatchOutcome(winner_slots={0}, pass_slots={0}, tie_all=False, raw_command="a"),
                    target_dir=tmp_dir,
                )

                # Two files vanish from disk; their DB rows remain.
                os.remove(tmp_path / "b.txt")
                os.remove(tmp_path / "d.txt")

                before = self._elos_by_path(conn)
                survivors = ["a.txt", "c.txt", "e.txt"]
                shift = sum(before[p] - 1000 for p in ("b.txt", "d.txt")) / len(survivors)

                removed = handle_refresh_command(conn, tmp_dir, confirm=False)

            # Stale rows gone, two removed.
            self.assertEqual(len(removed), 2)
            after = self._elos_by_path(conn)
            self.assertEqual(set(after.keys()), set(survivors))

            # Total Elo restored to (remaining N) x 1000.
            self.assertAlmostEqual(sum(after.values()), len(survivors) * 1000, places=6)

            # Redistribution is a uniform shift -> pairwise gaps preserved.
            for p in survivors:
                self.assertAlmostEqual(after[p], before[p] + shift, places=6)

    def test_refresh_noop_when_all_present(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["a.txt", "b.txt"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["a.txt", "b.txt"]:
                add_file_to_db(conn, name)

            before = self._elos_by_path(conn)
            with patch("local_elo.elo.sys.platform", "linux"):
                removed = handle_refresh_command(conn, tmp_dir, confirm=False)

            self.assertEqual(removed, set())
            self.assertEqual(self._elos_by_path(conn), before)

    def test_refresh_ignores_pattern_keeps_existing_files(self):
        # A file that exists but wouldn't match a filter must NOT be removed.
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["keep.png", "gone.png"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["keep.png", "gone.png"]:
                add_file_to_db(conn, name)

            os.remove(tmp_path / "gone.png")
            with patch("local_elo.elo.sys.platform", "linux"):
                removed = handle_refresh_command(conn, tmp_dir, confirm=False)

            self.assertEqual(len(removed), 1)
            self.assertEqual(set(self._elos_by_path(conn).keys()), {"keep.png"})

    def _setup_one_stale(self, tmp_dir):
        tmp_path = Path(tmp_dir)
        for name in ["keep.txt", "gone.txt"]:
            (tmp_path / name).write_text("x", encoding="utf-8")
        conn = init_db(tmp_dir)
        for name in ["keep.txt", "gone.txt"]:
            add_file_to_db(conn, name)
        os.remove(tmp_path / "gone.txt")
        return conn

    def test_refresh_confirm_no_cancels(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            conn = self._setup_one_stale(tmp_dir)
            before = self._elos_by_path(conn)
            with patch("local_elo.elo.sys.platform", "linux"), \
                 patch("builtins.input", return_value="n"):
                removed = handle_refresh_command(conn, tmp_dir)  # confirm defaults True

            # Nothing deleted, Elos untouched.
            self.assertEqual(removed, set())
            self.assertEqual(set(self._elos_by_path(conn).keys()), {"keep.txt", "gone.txt"})
            self.assertEqual(self._elos_by_path(conn), before)

    def test_refresh_confirm_blank_cancels(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            conn = self._setup_one_stale(tmp_dir)
            with patch("local_elo.elo.sys.platform", "linux"), \
                 patch("builtins.input", return_value=""):
                removed = handle_refresh_command(conn, tmp_dir)

            self.assertEqual(removed, set())
            self.assertEqual(set(self._elos_by_path(conn).keys()), {"keep.txt", "gone.txt"})

    def test_refresh_confirm_yes_proceeds(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            conn = self._setup_one_stale(tmp_dir)
            with patch("local_elo.elo.sys.platform", "linux"), \
                 patch("builtins.input", return_value="y"):
                removed = handle_refresh_command(conn, tmp_dir)

            self.assertEqual(len(removed), 1)
            self.assertEqual(set(self._elos_by_path(conn).keys()), {"keep.txt"})

    def test_redistribute_elo_delta_skip_none_updates_all(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
                (tmp_path / name).write_text("x", encoding="utf-8")

            conn = init_db(tmp_dir)
            for name in ["a.txt", "b.txt", "c.txt", "d.txt"]:
                add_file_to_db(conn, name)

            with patch("local_elo.elo.sys.platform", "linux"):
                redistribute_elo_delta(conn, 40.0, target_dir=tmp_dir)

            cursor = conn.cursor()
            cursor.execute("SELECT elo FROM files")
            elos = [row[0] for row in cursor.fetchall()]
            self.assertTrue(all(abs(elo - 1010.0) < 1e-6 for elo in elos))


class RenameCommandTests(unittest.TestCase):
    PATTERN = r".*\.(png|jpg)$"

    def _setup(self, tmp_dir, names):
        tmp_path = Path(tmp_dir)
        for name in names:
            (tmp_path / name).write_text("x", encoding="utf-8")
        conn = init_db(tmp_dir)
        for name in names:
            add_file_to_db(conn, name)
        return conn

    def _db_paths(self, conn):
        cursor = conn.cursor()
        cursor.execute("SELECT path FROM files")
        return {row[0] for row in cursor.fetchall()}

    def test_letter_keeps_original_extension(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            names = ["foo.png", "bar.png", "baz.png"]
            conn = self._setup(tmp_dir, names)

            updated = handle_rename_command(
                conn, "ren a NewName", tmp_dir, self.PATTERN, list(names)
            )

            self.assertTrue((Path(tmp_dir) / "NewName.png").exists())
            self.assertFalse((Path(tmp_dir) / "foo.png").exists())
            self.assertEqual(updated[0], "NewName.png")
            self.assertIn("NewName.png", self._db_paths(conn))

    def test_letter_with_explicit_extension(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            names = ["foo.png", "bar.png"]
            conn = self._setup(tmp_dir, names)

            updated = handle_rename_command(
                conn, "ren b NewName.jpg", tmp_dir, self.PATTERN, list(names)
            )

            self.assertTrue((Path(tmp_dir) / "NewName.jpg").exists())
            self.assertEqual(updated[1], "NewName.jpg")

    def test_exact_filename_infers_extension(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            names = ["foo.png", "bar.png"]
            conn = self._setup(tmp_dir, names)

            updated = handle_rename_command(
                conn, "ren foo.png renamed", tmp_dir, self.PATTERN, list(names)
            )

            self.assertTrue((Path(tmp_dir) / "renamed.png").exists())
            self.assertEqual(updated[0], "renamed.png")

    def test_wildcard_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            names = ["one.png", "two.png"]
            conn = self._setup(tmp_dir, names)

            handle_rename_command(
                conn, "ren *.png *.jpg", tmp_dir, self.PATTERN, list(names)
            )

            self.assertTrue((Path(tmp_dir) / "one.jpg").exists())
            self.assertTrue((Path(tmp_dir) / "two.jpg").exists())
            self.assertFalse((Path(tmp_dir) / "one.png").exists())

    def test_out_of_range_letter_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            names = ["foo.png", "bar.png"]
            conn = self._setup(tmp_dir, names)

            updated = handle_rename_command(
                conn, "ren z x", tmp_dir, self.PATTERN, list(names)
            )

            self.assertEqual(updated, names)
            self.assertEqual(self._db_paths(conn), set(names))
            self.assertTrue((Path(tmp_dir) / "foo.png").exists())


if __name__ == "__main__":
    unittest.main()
