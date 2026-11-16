#!/bin/zsh
# Double-click to connect the inbox that receives finn.no / LinkedIn job alerts.
cd "$(dirname "$0")" || exit 1
python3 finder.py --connect-email
echo
echo "Ferdig. Du kan lukke dette vinduet."
