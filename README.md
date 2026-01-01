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
