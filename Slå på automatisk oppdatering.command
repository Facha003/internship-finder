#!/bin/zsh
# Double-click to make the finder run by itself every 2 hours (and at login).
# It runs quietly and shows a notification when new internship positions appear.
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3)"
LABEL="no.bachlor.finder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/bachelor-finder.log"
if [ -z "$PY" ]; then echo "python3 was not found."; exit 1; fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-u</string>
    <string>$DIR/finder.py</string>
    <string>--background</string>
  </array>
  <key>StartInterval</key><integer>7200</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
if launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
  echo "Automatic updates are ON: every 2 hours while you are logged in."
  echo "Log: $LOG"
else
  echo "Could not turn on automatic updates."; exit 1
fi
