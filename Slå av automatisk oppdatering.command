#!/bin/zsh
# Double-click to stop the automatic background runs.
LABEL="no.bachlor.finder"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Automatic updates are OFF."
