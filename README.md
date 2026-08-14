# Local Elo

A simple, portable CLI tool for ranking files using Elo ratings through pairwise comparisons.

## The Problem

Humans are terrible at ranking things, but excellent at comparing pairs. Instead of agonizing over whether something deserves to be #3 or #7 on your list, just answer simple questions: "Which do I prefer?"

## Use Cases

- **Photo curation**: Pick the best photos from hundreds of vacation shots for a family album
- **Reading list**: Decide which articles or books to read next based on your actual interest
- **Music playlist**: Create a "best of" playlist by comparing songs pairwise
- **Video selection**: Choose which videos to keep, share, or feature
- **Design review**: Rank design mockups, logos, or creative work
- **Writing samples**: Organize your best articles, essays, or code samples
- **Recipe collection**: Figure out which recipes you actually want to cook
- **Anything**: Any collection where you need to find the best items but ranking is hard

## How It Works

1. Point the tool at a folder of files
2. It shows you two files at a time
3. You pick which one is better (or declare a tie)
4. The Elo rating system does the math
5. Over time, your true preferences emerge as a ranked leaderboard

The algorithm intelligently selects matchups, favoring close contests to efficiently find the true ranking.

## Installation

Just download `local_elo.py` - it's a single file with no dependencies beyond Python 3's standard library.

```bash
# Make it executable (optional)
chmod +x local_elo.py
```

## Usage

### Basic usage (rank all files in current directory)
```bash
python3 local_elo.py
```

### Filter by file type
```bash
# Only .jpg images
python3 local_elo.py "\.jpg$"

# Only .txt or .md files
python3 local_elo.py "\.(txt|md)$"

# Only .mp4 videos
python3 local_elo.py "\.mp4$"
```

### Refresh the database
If you've deleted or moved files outside the tool, their database entries become
stale. Run a one-shot refresh to purge any entries whose files no longer exist on
disk and redistribute their Elo across the survivors (total Elo always stays
N × 1000), then exit:
```bash
python3 local_elo.py --refresh
```
Refresh first lists the stale entries it's about to delete and asks for
confirmation (`y/N`), so you can preview the cleanup and answer `N` to abort
without changes. You can also type `refresh` during a session to do the same
without exiting.
Refresh never adds files that exist on disk but aren't yet tracked — those are
picked up automatically as you play.

### Stored settings
The command-line options can be saved per-directory in a `local_elo.json` file
alongside the database, so you don't have to retype them every run. On startup the
tool reads that file (if present) from the target directory and uses it for the
options; anything you pass on the command line overrides the stored value.

Precedence is **built-in defaults < `local_elo.json` < command-line flags**. The
file is entirely optional — if it's missing, the tool behaves exactly as before.

You don't have to hand-write it: type `store` during a session to save the current
settings to `<target_dir>/local_elo.json`. Example:
```json
{
  "extensions": "py,js,ts",
  "knockout": true,
  "link_pattern": "linkedin.com/in/*",
  "match_size": 4,
  "pool_size": "200/50",
  "power": "10/5"
}
```
(`target_dir` and `--refresh` are never stored — the former locates the file, the
latter is a one-shot action.)

### Merged projects
Rank files that live across several folders together, without copying them:

```bash
python3 local_elo.py merge -o merged_pool -i ~/photos/trip1 ~/photos/trip2 ~/photos/trip3
```

This assembles `merged_pool/` out of **symlinks** to every file in the input
folders (so no disk is wasted and `o` still opens the originals), then you rank
it like any other folder: `python3 local_elo.py merged_pool`.

- **Disambiguation**: when the same filename appears in more than one input
  folder, the symlink is named `name (source folder).ext`; unique names stay
  plain. (A disambiguated name is kept on later re-runs even if the collision
  goes away, since links are tracked by their target — this is also what lets
  you rename a symlink inside the merged folder without a re-merge undoing it.)
- **Seeded Elo**: each merged file starts at the Elo it had in its source
  folder's `local_elo.db` (record reset to 0). Files with no prior rating start
  at 1000. Files that already exist in the merged folder keep their current
  Elo — so ratings earned inside the merged pool are never reset by a re-merge.
- **Merged settings**: the input folders' `local_elo.json` settings are combined
  (first folder wins per option; `extensions` are unioned).
- **Idempotent**: re-run `merge` any time — new source files gain symlinks,
  removed ones have their symlinks (and db rows) cleaned up, and correct links
  are left untouched. Regular files you add to the merged folder are never
  touched.
- **Rename propagation**: because a symlink breaks if you rename its target,
  each source folder records which merged folders consume it
  (`local_elo_merges.json`). When you `ren` a file in a source folder, the merge
  updates automatically (its symlink repoints and its db entry is renamed). A
  full `merge` re-run heals anything changed outside the tool.

### During gameplay
```
A: photo1.jpg (1520) vs B: photo2.jpg (1480)
Your choice (A/B/=/top [N]):
```

**Commands:**
- `A` - File A is better
- `B` - File B is better
- `=` - They're equally good (tie)
- `top` - Show top 10 files
- `top 20` - Show top 20 files
- `refresh` - Purge database entries whose files no longer exist on disk and recalculate remaining Elos (lists what will be deleted and asks for confirmation first)
- `store` - Save the current settings to `local_elo.json` in the target directory so future runs reuse them
- `Ctrl+C` - Exit

### Example session
```bash
cd ~/Photos/Vacation2025
python3 /path/to/local_elo.py "\.jpg$"

# Compare pairs, build rankings
A: beach_sunset.jpg (1000) vs B: mountain_view.jpg (1000)
Your choice: A

A: beach_sunset.jpg (1016) vs B: family_dinner.jpg (1000)
Your choice: B

# Check progress
Your choice: top 5

Top 5 Files:
1. 1532 (12W-3L-1T) beach_sunset.jpg
2. 1489 (8W-4L-2T) family_dinner.jpg
3. 1456 (7W-5L-0T) mountain_view.jpg
4. 1423 (6W-7L-1T) pool_party.jpg
5. 1401 (5W-8L-0T) hotel_lobby.jpg
```

## How Elo Ratings Work

- Every file starts at **1000 Elo**
- When A beats B, A gains points and B loses points
- The amount depends on the rating difference:
  - Upset wins (underdog beats favorite) transfer more points
  - Expected wins transfer fewer points
- Ties transfer points toward equality
- **K-factor of 32** means ratings respond reasonably fast to new comparisons

The beauty: you don't need to compare everything to everything. After enough comparisons, the ratings converge to reflect your true preferences.

## Features

- **Single file**: Copy `local_elo.py` anywhere, no installation needed
- **Persistent database**: Your rankings are saved in `local_elo.db` (SQLite)
- **Smart matchups**: Algorithm favors close contests for efficient ranking
- **Regex filtering**: Compare only the files you want
- **Game history**: All comparisons are saved for future reference
- **Portable**: Pure Python 3 standard library

## Technical Details

- **Default Elo**: 1000
- **K-factor**: 32
- **Tie handling**: Standard draw (0.5 points to each side)
- **Database**: SQLite (local_elo.db)
- **Matchup algorithm**:
  - First player: weighted by probability of beating average opponent
  - Second player: weighted by match closeness (probability of weaker beating stronger)

## Files

The tool creates/uses:
- `local_elo.db` - SQLite database with ratings and game history
- Ignores itself (`local_elo.py`) and the database when scanning files

## Tips

- **Start fresh**: Delete `local_elo.db` to reset all ratings
- **Quick decisions**: Don't overthink - your gut reactions work best
- **Regular checks**: Use `top` frequently to see how rankings evolve
- **Enough comparisons**: More comparisons = more accurate rankings (but you don't need to compare every pair)
- **Ties are okay**: Use `=` when files are genuinely equal to you

## License

MIT

## Why "Local Elo"?

Because it runs locally on your files using the Elo rating system. Simple as that.
