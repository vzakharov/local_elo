import json
import os
import sqlite3
import tempfile
import unittest

from local_elo.constants import DB_NAME, CONFIG_NAME, MERGES_NAME
from local_elo.db import init_db
from local_elo import merge


def _symlinks_ok():
    """Probe whether this platform/environment can create symlinks."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, 'target')
            with open(target, 'w') as f:
                f.write('x')
            link = os.path.join(tmp, 'link')
            os.symlink(target, link)
            return os.path.islink(link)
    except (OSError, NotImplementedError):
        return False


SYMLINKS_OK = _symlinks_ok()


def _touch(directory, name, content='data'):
    path = os.path.join(directory, name)
    with open(path, 'w') as f:
        f.write(content)
    return path


def _seed_source_db(directory, rows):
    """rows: {basename: elo}. Creates a local_elo.db with those files."""
    conn = init_db(directory)
    cur = conn.cursor()
    for name, elo in rows.items():
        cur.execute('INSERT INTO files (path, elo, wins, losses, ties) VALUES (?, ?, 5, 3, 1)',
                    (name, elo))
    conn.commit()
    conn.close()


def _out_db_paths(out_dir):
    conn = sqlite3.connect(os.path.join(out_dir, DB_NAME))
    try:
        cur = conn.cursor()
        cur.execute('SELECT path, elo, wins, losses, ties FROM files')
        return {row[0]: row[1:] for row in cur.fetchall()}
    finally:
        conn.close()


@unittest.skipUnless(SYMLINKS_OK, "symlinks unsupported in this environment")
class MergeSymlinkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.a = os.path.join(self.root, 'projA')
        self.b = os.path.join(self.root, 'projB')
        self.out = os.path.join(self.root, 'merged')
        os.makedirs(self.a)
        os.makedirs(self.b)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        return merge.merge_main(['-o', self.out, '-i', self.a, self.b])

    def test_basic_symlinks_created(self):
        _touch(self.a, 'one.txt')
        _touch(self.b, 'two.txt')
        self.assertEqual(self._run(), 0)
        link_a = os.path.join(self.out, 'one.txt')
        link_b = os.path.join(self.out, 'two.txt')
        self.assertTrue(os.path.islink(link_a))
        self.assertTrue(os.path.islink(link_b))
        self.assertEqual(os.path.realpath(link_a), os.path.realpath(os.path.join(self.a, 'one.txt')))

    def test_collision_disambiguation(self):
        _touch(self.a, 'dup.txt')
        _touch(self.b, 'dup.txt')
        self._run()
        self.assertTrue(os.path.islink(os.path.join(self.out, 'dup (projA).txt')))
        self.assertTrue(os.path.islink(os.path.join(self.out, 'dup (projB).txt')))
        self.assertFalse(os.path.exists(os.path.join(self.out, 'dup.txt')))

    def test_secondary_collision_numeric_fallback(self):
        # Two inputs whose trailing folder name is the same -> disambiguated
        # names would still collide, forcing a numeric suffix.
        c1 = os.path.join(self.root, 'x', 'same')
        c2 = os.path.join(self.root, 'y', 'same')
        os.makedirs(c1)
        os.makedirs(c2)
        _touch(c1, 'dup.txt')
        _touch(c2, 'dup.txt')
        self.assertEqual(merge.merge_main(['-o', self.out, '-i', c1, c2]), 0)
        names = sorted(n for n in os.listdir(self.out) if n.endswith('.txt'))
        self.assertEqual(names, ['dup (same) (2).txt', 'dup (same).txt'])

    def test_idempotent_rerun(self):
        _touch(self.a, 'one.txt')
        _touch(self.b, 'two.txt')
        self._run()
        before = {n: os.readlink(os.path.join(self.out, n))
                  for n in os.listdir(self.out) if os.path.islink(os.path.join(self.out, n))}
        self._run()
        after = {n: os.readlink(os.path.join(self.out, n))
                 for n in os.listdir(self.out) if os.path.islink(os.path.join(self.out, n))}
        self.assertEqual(before, after)

    def test_new_source_added_on_rerun(self):
        _touch(self.a, 'one.txt')
        self._run()
        _touch(self.a, 'later.txt')
        self._run()
        self.assertTrue(os.path.islink(os.path.join(self.out, 'later.txt')))

    def test_dangling_and_orphaned_links_removed_with_db_row(self):
        _touch(self.a, 'one.txt')
        _touch(self.a, 'gone.txt')
        self._run()
        # Elo rows exist for both.
        self.assertIn('gone.txt', _out_db_paths(self.out))
        # Delete a source file and re-merge.
        os.remove(os.path.join(self.a, 'gone.txt'))
        self._run()
        self.assertFalse(os.path.lexists(os.path.join(self.out, 'gone.txt')))
        self.assertNotIn('gone.txt', _out_db_paths(self.out))
        # Survivor untouched.
        self.assertTrue(os.path.islink(os.path.join(self.out, 'one.txt')))

    def test_disambiguated_name_kept_when_collision_resolves(self):
        # Match-by-target preserves names (so in-merge renames survive): a link
        # keeps its disambiguated name even after the collision goes away.
        _touch(self.a, 'dup.txt')
        _touch(self.b, 'dup.txt')
        self._run()
        self.assertTrue(os.path.islink(os.path.join(self.out, 'dup (projA).txt')))
        os.remove(os.path.join(self.b, 'dup.txt'))
        self._run()
        # A's link is kept as-is; B's orphaned link is removed.
        self.assertTrue(os.path.islink(os.path.join(self.out, 'dup (projA).txt')))
        self.assertFalse(os.path.lexists(os.path.join(self.out, 'dup (projB).txt')))

    def test_freed_name_reclaimed_by_new_source(self):
        # Genuine reclaim: an orphaned link frees a name that a *newly* linked
        # source wants. Removal runs before create, so the name is reusable.
        _touch(self.b, 'x.txt')
        self._run()
        self.assertEqual(os.path.realpath(os.path.join(self.out, 'x.txt')),
                         os.path.realpath(os.path.join(self.b, 'x.txt')))
        # B loses x.txt; A gains a (different) x.txt.
        os.remove(os.path.join(self.b, 'x.txt'))
        _touch(self.a, 'x.txt')
        self._run()
        self.assertTrue(os.path.islink(os.path.join(self.out, 'x.txt')))
        self.assertEqual(os.path.realpath(os.path.join(self.out, 'x.txt')),
                         os.path.realpath(os.path.join(self.a, 'x.txt')))

    def test_real_file_not_deleted_or_overwritten(self):
        _touch(self.a, 'one.txt')
        self._run()
        # A real (non-symlink) file occupies a name the merge would want.
        _touch(self.out, 'mine.txt', 'keep me')
        _touch(self.b, 'mine.txt')
        self._run()
        self.assertFalse(os.path.islink(os.path.join(self.out, 'mine.txt')))
        with open(os.path.join(self.out, 'mine.txt')) as f:
            self.assertEqual(f.read(), 'keep me')       # not overwritten
        # The unrelated real file also survives the removal pass.
        self.assertTrue(os.path.isfile(os.path.join(self.out, 'mine.txt')))

    def test_elo_seeded_from_source_record_reset(self):
        _touch(self.a, 'one.txt')
        _seed_source_db(self.a, {'one.txt': 1500})
        self._run()
        rows = _out_db_paths(self.out)
        elo, wins, losses, ties = rows['one.txt']
        self.assertEqual(elo, 1500)
        self.assertEqual((wins, losses, ties), (0, 0, 0))   # record reset

    def test_existing_out_row_preserved(self):
        _touch(self.a, 'one.txt')
        _seed_source_db(self.a, {'one.txt': 1500})
        # Pre-create the out db row with a different elo (as if it competed).
        os.makedirs(self.out)
        conn = init_db(self.out)
        conn.execute('INSERT INTO files (path, elo) VALUES (?, ?)', ('one.txt', 1234))
        conn.commit()
        conn.close()
        self._run()
        self.assertEqual(_out_db_paths(self.out)['one.txt'][0], 1234)

    def test_elo_default_without_source_db(self):
        _touch(self.a, 'one.txt')          # no source db at all
        self._run()
        self.assertEqual(_out_db_paths(self.out)['one.txt'][0], 1000)

    def test_registry_written_and_idempotent(self):
        _touch(self.a, 'one.txt')
        self._run()
        reg_path = os.path.join(self.a, MERGES_NAME)
        self.assertTrue(os.path.exists(reg_path))
        with open(reg_path) as f:
            self.assertEqual(json.load(f), [os.path.abspath(self.out)])
        self._run()
        with open(reg_path) as f:
            self.assertEqual(json.load(f), [os.path.abspath(self.out)])   # no dup

    def test_out_equals_input_errors(self):
        _touch(self.a, 'one.txt')
        self.assertEqual(merge.merge_main(['-o', self.a, '-i', self.a]), 1)

    def test_out_dir_created_if_missing(self):
        _touch(self.a, 'one.txt')
        self.assertFalse(os.path.exists(self.out))
        self._run()
        self.assertTrue(os.path.isdir(self.out))


@unittest.skipUnless(SYMLINKS_OK, "symlinks unsupported in this environment")
class MergeConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.a = os.path.join(self.root, 'a')
        self.b = os.path.join(self.root, 'b')
        self.out = os.path.join(self.root, 'out')
        os.makedirs(self.a)
        os.makedirs(self.b)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_config(self, directory, cfg):
        with open(os.path.join(directory, CONFIG_NAME), 'w') as f:
            json.dump(cfg, f)

    def test_first_wins_and_extensions_union(self):
        _touch(self.a, 'x.txt')
        _touch(self.b, 'y.txt')
        self._write_config(self.a, {'match_size': 4, 'extensions': 'py,js'})
        self._write_config(self.b, {'match_size': 8, 'extensions': 'js,ts'})
        merge.merge_main(['-o', self.out, '-i', self.a, self.b])
        with open(os.path.join(self.out, CONFIG_NAME)) as f:
            merged = json.load(f)
        self.assertEqual(merged['match_size'], 4)                 # first wins
        self.assertEqual(merged['extensions'], 'py,js,ts')        # union, order-preserving


@unittest.skipUnless(SYMLINKS_OK, "symlinks unsupported in this environment")
class RenamePropagationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.src = os.path.join(self.root, 'src')
        self.out = os.path.join(self.root, 'merged')
        os.makedirs(self.src)

    def tearDown(self):
        self._tmp.cleanup()

    def test_source_rename_propagates_to_merge(self):
        _touch(self.src, 'old.txt')
        merge.merge_main(['-o', self.out, '-i', self.src])
        # Simulate a source-side rename (file + registry drive propagation).
        os.rename(os.path.join(self.src, 'old.txt'), os.path.join(self.src, 'new.txt'))
        merge.propagate_rename_to_merges(self.src, [('old.txt', 'new.txt')])
        link = os.path.join(self.out, 'new.txt')
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.lexists(os.path.join(self.out, 'old.txt')))
        self.assertEqual(os.path.abspath(os.readlink(link)),
                         os.path.abspath(os.path.join(self.src, 'new.txt')))
        self.assertIn('new.txt', _out_db_paths(self.out))
        self.assertNotIn('old.txt', _out_db_paths(self.out))

    def test_propagation_preserves_disambiguation_suffix(self):
        other = os.path.join(self.root, 'other')
        os.makedirs(other)
        _touch(self.src, 'dup.txt')
        _touch(other, 'dup.txt')
        merge.merge_main(['-o', self.out, '-i', self.src, other])
        self.assertTrue(os.path.islink(os.path.join(self.out, 'dup (src).txt')))
        os.rename(os.path.join(self.src, 'dup.txt'), os.path.join(self.src, 'renamed.txt'))
        merge.propagate_rename_to_merges(self.src, [('dup.txt', 'renamed.txt')])
        self.assertTrue(os.path.islink(os.path.join(self.out, 'renamed (src).txt')))
        self.assertFalse(os.path.lexists(os.path.join(self.out, 'dup (src).txt')))

    def test_propagation_to_missing_target_is_noop(self):
        _touch(self.src, 'one.txt')
        merge.merge_main(['-o', self.out, '-i', self.src])
        import shutil
        shutil.rmtree(self.out)
        # Should not raise even though the merge folder is gone.
        merge.propagate_rename_to_merges(self.src, [('one.txt', 'two.txt')])


@unittest.skipUnless(SYMLINKS_OK, "symlinks unsupported in this environment")
class RenameSurvivesRemergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.src = os.path.join(self.root, 'src')
        self.out = os.path.join(self.root, 'merged')
        os.makedirs(self.src)

    def tearDown(self):
        self._tmp.cleanup()

    def test_in_merge_rename_survives_rerun(self):
        target = _touch(self.src, 'one.txt')
        merge.merge_main(['-o', self.out, '-i', self.src])
        # Rename the link inside the merge folder (points at same source).
        os.rename(os.path.join(self.out, 'one.txt'), os.path.join(self.out, 'renamed.txt'))
        # Re-merge: matched by target, so the renamed link is kept, not recreated.
        merge.merge_main(['-o', self.out, '-i', self.src])
        self.assertTrue(os.path.islink(os.path.join(self.out, 'renamed.txt')))
        self.assertFalse(os.path.lexists(os.path.join(self.out, 'one.txt')))


if __name__ == '__main__':
    unittest.main()
