# Local Elo Examples

This directory contains example scripts for customizing how Local Elo opens files.

## Custom Startup Scripts

Local Elo checks for custom startup scripts in the target directory (where your media files are located):

- **macOS/Linux**: `elo_start.sh`
- **Windows**: `elo_start.bat`

If found, these scripts will be used instead of the default system commands when you press 'o' to open files.

### How it works

1. Place a startup script (`elo_start.sh` or `elo_start.bat`) in your target directory
2. Make it executable (Unix: `chmod +x elo_start.sh`)
3. The script will receive the absolute path to the file as its first argument
4. When you press 'o' in Local Elo, your custom script will be called

### Basic Example: elo_start.sh

```bash
#!/bin/bash
# Copy this to your target directory and customize

# Default behavior - open with system default app
if [[ "$OSTYPE" == "darwin"* ]]; then
    open "$1"
else
    xdg-open "$1"
fi
```

### QuickTime Player with Looping (macOS)

We provide a ready-to-use solution for opening files in QuickTime Player with looping enabled.

**Quick Setup (easiest method):**

Simply copy the `elo_start_quicktime.sh` script to your target directory as `elo_start.sh`:

```bash
cp examples/elo_start_quicktime.sh /path/to/your/media/directory/elo_start.sh
```

That's it! Now when you press 'o' in Local Elo, files will open in QuickTime Player and start playing in loop mode.

**How it works:**

The `elo_start_quicktime.sh` script automatically finds and calls the `quicktime_loop.applescript` AppleScript, which:
- Opens the file in QuickTime Player
- Enables looping mode
- Starts playback immediately

**Manual setup (if you want to customize):**

If you prefer to create your own custom script:

1. Create `elo_start.sh` in your target directory:
   ```bash
   #!/bin/bash
   osascript /full/path/to/examples/quicktime_loop.applescript "$1"
   ```

2. Make it executable:
   ```bash
   chmod +x elo_start.sh
   ```

### Windows Example: elo_start.bat

```batch
@echo off
REM Example Windows startup script
REM Place in your target directory

REM Open with default application
start "" "%~1"

REM Or use a specific application:
REM "C:\Program Files\VideoLAN\VLC\vlc.exe" "%~1"
```

### Advanced Examples

**VLC Media Player (macOS/Linux)**:
```bash
#!/bin/bash
/Applications/VLC.app/Contents/MacOS/VLC --loop "$1"
# Linux: vlc --loop "$1"
```

**mpv with specific settings**:
```bash
#!/bin/bash
mpv --loop=inf --volume=50 "$1"
```

## Troubleshooting

- **Script not being used**: Check that the script is in the target directory (not the Local Elo installation directory)
- **Permission denied**: Make sure the script is executable (`chmod +x elo_start.sh`)
- **AppleScript errors**: Ensure you're using the full path to the AppleScript in your elo_start.sh
