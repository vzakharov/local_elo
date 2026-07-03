import json
import os
import tempfile
import unittest

from local_elo.constants import CONFIG_NAME
from local_elo.knockout import PoolConfig
from local_elo.settings import (
    load_config, resolve_settings, save_config, settings_to_config, build_parser,
)


def _write_config(target_dir, config):
    with open(os.path.join(target_dir, CONFIG_NAME), 'w') as f:
        json.dump(config, f)


class ResolveSettingsTests(unittest.TestCase):
    def test_defaults_when_no_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = resolve_settings([tmp])
            self.assertEqual(args.target_dir, tmp)
            self.assertEqual(args.match_size, 2)
            self.assertEqual(args.power, (2.0, 2.0))
            self.assertFalse(args.knockout)
            self.assertIsNone(args.pool_size)
            self.assertIsNone(args.extensions)

    def test_stored_settings_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {
                "match_size": 4,
                "knockout": True,
                "power": "10/5",
                "pool_size": "200/50",
                "extensions": "py,js",
                "link_pattern": "linkedin.com/in/*",
            })
            args = resolve_settings([tmp])
            self.assertEqual(args.match_size, 4)
            self.assertTrue(args.knockout)
            self.assertEqual(args.power, (10.0, 5.0))
            self.assertEqual(args.pool_size, PoolConfig(200, 50))
            self.assertEqual(args.extensions, "py,js")
            self.assertEqual(args.link_pattern, "linkedin.com/in/*")

    def test_cli_overrides_stored(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"match_size": 4, "knockout": True})
            args = resolve_settings([tmp, "-m", "2"])
            # CLI wins for match_size...
            self.assertEqual(args.match_size, 2)
            # ...but the stored knockout flag (not overridden) still applies.
            self.assertTrue(args.knockout)

    def test_null_value_treated_as_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"pool_size": None, "match_size": 3})
            args = resolve_settings([tmp])
            self.assertIsNone(args.pool_size)
            self.assertEqual(args.match_size, 3)

    def test_unknown_key_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"bogus": 123, "match_size": 5})
            args = resolve_settings([tmp])
            self.assertEqual(args.match_size, 5)
            self.assertFalse(hasattr(args, "bogus"))

    def test_target_dir_and_refresh_not_read_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"target_dir": "/somewhere/else", "refresh": True})
            args = resolve_settings([tmp])
            self.assertEqual(args.target_dir, tmp)
            self.assertFalse(args.refresh)

    def test_invalid_json_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, CONFIG_NAME), 'w') as f:
                f.write("{not valid json")
            with self.assertRaises(SystemExit):
                resolve_settings([tmp])

    def test_invalid_value_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_config(tmp, {"power": "abc"})
            with self.assertRaises(SystemExit):
                resolve_settings([tmp])


class StoreRoundTripTests(unittest.TestCase):
    def test_store_then_reload_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = [tmp, "-k", "-p", "10/5", "-m", "4", "-n", "32", "-e", "py,js", "-l", "x/*"]
            first = resolve_settings(argv)

            config = settings_to_config(first, build_parser())
            path = save_config(tmp, config)
            self.assertTrue(os.path.exists(path))

            # Re-resolve from the stored file alone (no CLI flags).
            second = resolve_settings([tmp])
            self.assertEqual(second.knockout, first.knockout)
            self.assertEqual(second.power, first.power)
            self.assertEqual(second.match_size, first.match_size)
            self.assertEqual(second.pool_size, first.pool_size)
            self.assertEqual(second.extensions, first.extensions)
            self.assertEqual(second.link_pattern, first.link_pattern)

    def test_store_snapshot_is_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = resolve_settings([tmp])
            config = settings_to_config(args, build_parser())
            # Every eligible param present; target_dir / refresh excluded.
            self.assertEqual(
                set(config),
                {"extensions", "knockout", "power", "pool_size", "link_pattern", "match_size"},
            )
            self.assertNotIn("target_dir", config)
            self.assertNotIn("refresh", config)

    def test_saved_file_reloads_via_load_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            save_config(tmp, {"match_size": 7})
            self.assertEqual(load_config(tmp), {"match_size": 7})


if __name__ == "__main__":
    unittest.main()
