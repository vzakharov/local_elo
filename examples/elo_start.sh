#!/bin/bash
# Example startup script for macOS/Linux
# This script receives an absolute file path as the first argument
# Customize this script to control how files are opened

# Default: use the system's default application
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    open "$1"
else
    # Linux
    xdg-open "$1"
fi
