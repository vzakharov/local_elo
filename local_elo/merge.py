"""The ``merge`` subcommand: assemble a merged-project folder from several
source folders using symlinks, seeded with each file's existing Elo.

    local_elo.py merge -o <out_folder> -i <folder1> <folder2> [<folder3> ...]

For every file in every input folder a symlink is created in the out folder
(disambiguated as ``<stem> (<folder>)<ext>`` only when a basename collides
across inputs). The command is idempotent: re-running adds symlinks for
newly-appeared source files, removes symlinks whose source is gone, and leaves
correct links untouched. Symlinks are matched to sources by their *target*
path, not their name, so renames survive a re-merge.

Each merged file's Elo is seeded from its source folder's ``local_elo.db``
(win/loss/tie record reset to zero), unless a row for that name already exists
in the out folder — those keep their (possibly evolved) Elo.

Each input folder also gets a small ``local_elo_merges.json`` registry listing
the out folders that consume it, so a rename made in a source folder can be
propagated directly to every merge (see ``propagate_rename_to_merges``).
"""

import argparse
import json
import os
import sqlite3
import sys

from .constants import DB_NAME, CONFIG_NAME, MERGES_NAME, DEFAULT_ELO
from .settings import load_config, save_config
from .files import discover_files
from .db import init_db
from .colors import green, yellow, red, cyan, dim, bold


def build_merge_parser() -> argparse.ArgumentParser:
    """Build the parser for the ``merge`` subcommand."""
    parser = argparse.ArgumentParser(
        prog='local_elo.py merge',
        description='Assemble a merged-project folder of symlinks from several source folders.',
    )
    parser.add_argument('-o', '--out', dest='out', required=True,
                        help='Output folder for the merged project (created if missing).')
    parser.add_argument('-i', '--inputs', dest='inputs', nargs='+', required=True,
                        help='One or more source folders to merge.')
    return parser


# --------------------------------------------------------------------------- #
# Symlink planning & disambiguation
# --------------------------------------------------------------------------- #

def _folder_label(input_dir: str) -> str:
    """The disambiguation label for an input dir: its trailing folder name."""
    return os.path.basename(os.path.normpath(os.path.abspath(input_dir)))


def _dedupe_name(name: str, taken: set) -> str:
    """Return ``name`` if free, else ``stem (2)ext``, ``stem (3)ext`` … until free."""
    if name not in taken:
        return name
    stem, ext = os.path.splitext(name)
    counter = 2
    while True:
        candidate = f"{stem} ({counter}){ext}"
        if candidate not in taken:
            return candidate
        counter += 1


def plan_symlinks(input_dirs):
    """Compute the desired symlink set for the given input dirs.

    Returns ``(desired, meta)`` where:
      * ``desired`` maps ``src_abs -> name`` (the merged symlink name),
      * ``meta`` maps ``name -> (input_dir, basename, src_abs)`` for seeding.

    A basename that appears in more than one input folder collides and is
    disambiguated as ``<stem> (<folder>)<ext>``. A final dedupe pass over all
    names resolves any residual clashes with a numeric ``(2)``/``(3)`` suffix.
    """
    # First list every source file, and count basenames across all inputs.
    listed = []  # (input_dir, basename)
    basename_counts = {}
    for input_dir in input_dirs:
        for basename in discover_files('.*', input_dir):
            listed.append((input_dir, basename))
            basename_counts[basename] = basename_counts.get(basename, 0) + 1

    desired = {}
    meta = {}
    taken = set()
    for input_dir, basename in listed:
        src_abs = os.path.abspath(os.path.join(input_dir, basename))
        if src_abs in desired:
            # Same absolute source reached twice (e.g. duplicate -i) — skip.
            continue
        if basename_counts[basename] > 1:
            stem, ext = os.path.splitext(basename)
            name = f"{stem} ({_folder_label(input_dir)}){ext}"
        else:
            name = basename
        name = _dedupe_name(name, taken)
        taken.add(name)
        desired[src_abs] = name
        meta[name] = (input_dir, basename, src_abs)
    return desired, meta


# --------------------------------------------------------------------------- #
# Idempotent symlink apply
# --------------------------------------------------------------------------- #

# Names that live in a merge folder but are never themselves ranked/symlinked.
_RESERVED_NAMES = {DB_NAME, CONFIG_NAME, MERGES_NAME,
                   'local_elo.py', 'elo_start.sh', 'elo_start.bat'}


def _existing_symlinks(out_dir):
    """Map ``normalized readlink target -> link name`` for symlinks in out_dir.

    Reserved names are ignored. Dangling links are included (readlink still
    returns the stored target string even when the target no longer exists).
    """
    result = {}
    for entry in os.listdir(out_dir):
        if entry in _RESERVED_NAMES:
            continue
        full = os.path.join(out_dir, entry)
        if os.path.islink(full):
            result[os.path.abspath(os.readlink(full))] = entry
    return result


def sync_symlinks(out_dir, desired):
    """Idempotently make out_dir's symlinks match ``desired`` (src_abs -> name).

    Matching is by *target* (source abs path), so a link renamed inside the
    merge folder is kept as-is rather than recreated under its original name.

    Returns ``(created, repointed, removed, skipped_real)`` — the last is the
    set of desired names blocked by a real (non-symlink) file, so seeding can
    skip them.
    """
    os.makedirs(out_dir, exist_ok=True)
    existing_by_target = _existing_symlinks(out_dir)

    created = []
    repointed = []
    removed = []
    skipped_real = set()

    # Removal pass FIRST: drop links whose target is no longer desired (orphaned
    # or dangling) and purge their db rows, so a freed name can be reclaimed by
    # a new source in the create pass below. Only ever touches symlinks, so
    # regular files are never deleted; no Elo redistribution (survivors stable).
    desired_targets = set(desired.keys())
    stale_names = [name for target, name in existing_by_target.items()
                   if target not in desired_targets]
    if stale_names:
        conn = init_db(out_dir)
        cur = conn.cursor()
        for name in stale_names:
            full = os.path.join(out_dir, name)
            try:
                os.unlink(full)
            except OSError as exc:
                print(yellow(f"  Could not remove stale symlink {cyan(name)}: {exc}"))
                continue
            cur.execute('DELETE FROM files WHERE path = ?', (name,))
            removed.append(name)
        conn.commit()
        conn.close()

    # Create pass: materialize any desired source not already linked. `taken`
    # reflects what actually remains on disk after the removal pass, so dedupe
    # only fires against names we won't remove (kept links, real files).
    taken = set(os.listdir(out_dir))
    for src_abs, name in desired.items():
        if src_abs in existing_by_target:
            # Kept link — its target is desired, so it survived the removal pass.
            continue
        full = os.path.join(out_dir, name)
        if os.path.islink(full) or os.path.exists(full):
            if not os.path.islink(full):
                # A real (non-symlink) file owns this name — never overwrite it.
                print(yellow(f"  Skipping {cyan(name)}: a real file already occupies that name"))
                skipped_real.add(name)
                continue
            # A surviving link already holds this exact name — give ours a fresh one.
            name = _dedupe_name(name, taken)
            full = os.path.join(out_dir, name)
        try:
            os.symlink(src_abs, full)
            created.append(name)
            taken.add(name)
        except OSError as exc:
            print(yellow(f"  Could not create symlink {cyan(name)}: {exc}"))

    return created, repointed, removed, skipped_real


# --------------------------------------------------------------------------- #
# Elo seeding
# --------------------------------------------------------------------------- #

def read_source_elos(input_dir):
    """Return ``{basename: elo}`` from a source folder's local_elo.db.

    Empty dict if the source has no db. Never creates a db in a source dir.
    """
    db_path = os.path.join(input_dir, DB_NAME)
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute('SELECT path, elo FROM files')
        return {path: elo for path, elo in cur.fetchall()}
    finally:
        conn.close()


def seed_elos(out_dir, meta, skipped_real):
    """Seed Elo for newly-added merged files from their source dbs.

    Rows already present in the out db (captured before any insert) keep their
    current Elo. New rows carry the source Elo with the win/loss/tie record
    reset to zero. Returns the number of rows seeded.
    """
    conn = init_db(out_dir)
    cur = conn.cursor()
    cur.execute('SELECT path FROM files')
    preexisting = {row[0] for row in cur.fetchall()}  # BEFORE any insert

    elo_cache = {}
    seeded = 0
    for name, (input_dir, basename, src_abs) in meta.items():
        if name in skipped_real or name in preexisting:
            continue
        if input_dir not in elo_cache:
            elo_cache[input_dir] = read_source_elos(input_dir)
        elo = elo_cache[input_dir].get(basename, DEFAULT_ELO)
        cur.execute(
            'INSERT OR IGNORE INTO files (path, elo, wins, losses, ties) '
            'VALUES (?, ?, 0, 0, 0)',
            (name, elo),
        )
        seeded += cur.rowcount
    conn.commit()
    conn.close()
    return seeded


# --------------------------------------------------------------------------- #
# Config merge
# --------------------------------------------------------------------------- #

def merge_configs(input_dirs, out_dir):
    """Merge input folders' local_elo.json into out_dir's.

    Per-key first-wins across inputs, except ``extensions`` which is the
    order-preserving, deduped union of every input's comma-separated list.
    Overwrites any existing out-dir config for determinism. Returns True if a
    config was written.
    """
    merged = {}
    ext_tokens = []
    for d in input_dirs:
        for key, value in load_config(d).items():
            if key == 'extensions' and isinstance(value, str):
                for tok in (t.strip() for t in value.split(',')):
                    if tok and tok not in ext_tokens:
                        ext_tokens.append(tok)
            elif key not in merged and value is not None:
                merged[key] = value
    if ext_tokens:
        merged['extensions'] = ','.join(ext_tokens)
    if merged:
        save_config(out_dir, merged)
        return True
    return False


# --------------------------------------------------------------------------- #
# Source-side merge registry
# --------------------------------------------------------------------------- #

def _registry_path(source_dir):
    return os.path.join(source_dir, MERGES_NAME)


def read_merge_registry(source_dir):
    """Return the list of out-dir abspaths that consume ``source_dir`` ([] if none)."""
    path = _registry_path(source_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def register_merge(source_dir, out_dir):
    """Idempotently record that ``out_dir`` is a merge target of ``source_dir``."""
    out_abs = os.path.abspath(out_dir)
    targets = read_merge_registry(source_dir)
    if out_abs in targets:
        return
    targets.append(out_abs)
    with open(_registry_path(source_dir), 'w') as f:
        json.dump(targets, f, indent=2)
        f.write('\n')


# --------------------------------------------------------------------------- #
# Rename propagation (direct apply)
# --------------------------------------------------------------------------- #

def _propagated_local_name(local_name, folder_label, new_name):
    """Compute a renamed link's new name, preserving any ``(folder)`` suffix.

    If the current link's stem ends with ``" (<folder>)"`` it was disambiguated,
    so keep that suffix with the new stem; otherwise use the new source name
    verbatim.
    """
    local_stem, local_ext = os.path.splitext(local_name)
    suffix = f" ({folder_label})"
    new_stem, new_ext = os.path.splitext(new_name)
    if local_stem.endswith(suffix):
        return f"{new_stem}{suffix}{new_ext}"
    return new_name


def propagate_rename_to_merges(source_dir, pairs):
    """Propagate ``(old_name, new_name)`` renames in source_dir to its merges.

    For each registered merge folder, finds the symlink pointing at the old
    source path (matched by target, so disambiguation doesn't matter), repoints
    it at the new source path under an appropriate name, and updates that
    merge's db row. Never raises — missing/unwritable targets are warned and
    skipped so the interactive loop is unaffected.
    """
    targets = read_merge_registry(source_dir)
    if not targets or not pairs:
        return
    folder_label = _folder_label(source_dir)
    affected = 0
    for target in targets:
        if not os.path.isdir(target):
            continue
        try:
            existing = _existing_symlinks(target)
        except OSError:
            continue
        taken = set(existing.values())
        conn = None
        changed = False
        for old_name, new_name in pairs:
            old_abs = os.path.abspath(os.path.join(source_dir, old_name))
            new_abs = os.path.abspath(os.path.join(source_dir, new_name))
            local_name = existing.get(old_abs)
            if local_name is None:
                continue  # this merge doesn't carry the renamed file
            new_local = _propagated_local_name(local_name, folder_label, new_name)
            if new_local != local_name:
                new_local = _dedupe_name(new_local, taken - {local_name})
            try:
                os.unlink(os.path.join(target, local_name))
                os.symlink(new_abs, os.path.join(target, new_local))
            except OSError as exc:
                print(yellow(f"  Could not update merge {cyan(target)}: {exc}"))
                continue
            # Keep our in-memory index consistent for later pairs.
            del existing[old_abs]
            existing[new_abs] = new_local
            taken.discard(local_name)
            taken.add(new_local)
            if conn is None:
                conn = init_db(target)
            conn.execute('UPDATE files SET path = ? WHERE path = ?', (new_local, local_name))
            changed = True
        if conn is not None:
            conn.commit()
            conn.close()
        if changed:
            affected += 1
    if affected:
        print(dim(f"  Propagated rename to {affected} merged folder(s)"))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def merge_main(argv=None):
    """Entry point for ``local_elo.py merge``. Returns a process exit code."""
    parser = build_merge_parser()
    args = parser.parse_args(argv)

    out_dir = args.out
    out_real = os.path.realpath(out_dir)

    # De-dupe input dirs (preserving order) and reject an input == the out dir.
    input_dirs = []
    seen = set()
    for d in args.inputs:
        real = os.path.realpath(d)
        if real == out_real:
            print(red(f"Error: input folder '{d}' is the same as the output folder"))
            return 1
        if not os.path.isdir(d):
            print(red(f"Error: input folder '{d}' does not exist or is not a directory"))
            return 1
        if real in seen:
            continue
        seen.add(real)
        input_dirs.append(d)

    if not input_dirs:
        print(red("Error: no valid input folders"))
        return 1

    desired, meta = plan_symlinks(input_dirs)
    created, repointed, removed, skipped_real = sync_symlinks(out_dir, desired)
    seeded = seed_elos(out_dir, meta, skipped_real)
    wrote_config = merge_configs(input_dirs, out_dir)

    for input_dir in input_dirs:
        register_merge(input_dir, out_dir)

    print(green(f"✓ Merged {len(input_dirs)} folder(s) into {bold(out_dir)}"))
    print(dim(
        f"  {len(created)} created, {len(removed)} removed, "
        f"{len(desired) - len(created) - len(repointed)} unchanged, "
        f"{seeded} seeded, config {'written' if wrote_config else 'unchanged'}"
    ))
    if skipped_real:
        print(yellow(f"  {len(skipped_real)} name(s) skipped (real files in the way)"))
    return 0
