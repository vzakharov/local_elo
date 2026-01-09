#!/bin/bash
# Startup script for opening files in QuickTime Player with looping enabled
# This script calls the quicktime_loop.applescript AppleScript
#
# Usage:
# 1. Copy this file to your target directory as 'elo_start.sh'
# 2. Make it executable: chmod +x elo_start.sh
#
# The script will automatically find the AppleScript in the local_elo examples directory

# Try to find the AppleScript in common locations
APPLESCRIPT_PATH=""

# Check if the AppleScript is in the same directory (both files copied together)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -f "$SCRIPT_DIR/quicktime_loop.applescript" ]; then
    APPLESCRIPT_PATH="$SCRIPT_DIR/quicktime_loop.applescript"
# Check in the local_elo examples directory (assuming it's in a known location)
elif [ -f "$HOME/Documents/GitHub/local_elo/examples/quicktime_loop.applescript" ]; then
    APPLESCRIPT_PATH="$HOME/Documents/GitHub/local_elo/examples/quicktime_loop.applescript"
# Check in the current working directory's examples folder
elif [ -f "./examples/quicktime_loop.applescript" ]; then
    APPLESCRIPT_PATH="./examples/quicktime_loop.applescript"
else
    echo "Error: Could not find quicktime_loop.applescript"
    echo "Please either:"
    echo "1. Copy quicktime_loop.applescript to the same directory as this script, or"
    echo "2. Edit this script and set APPLESCRIPT_PATH manually"
    exit 1
fi

# Call the QuickTime AppleScript with the file path
osascript "$APPLESCRIPT_PATH" "$1"
