#!/usr/bin/osascript
-- QuickTime Player with Loop Mode
-- This AppleScript opens a file in QuickTime Player and enables looping
-- Usage: osascript quicktime_loop.applescript "/path/to/file.mov"

on run argv
	set filePath to item 1 of argv

	tell application "QuickTime Player"
		activate

		-- Open the file and get reference to the document
		set theDoc to open POSIX file filePath

		-- Enable looping
		tell theDoc
			set looping to true
			-- Start playing
			play
		end tell
	end tell
end run
