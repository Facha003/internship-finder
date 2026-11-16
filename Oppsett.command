#!/bin/zsh
# Double-click to set your profile (programme, level, graduation year, areas).
cd "$(dirname "$0")" || exit 1
python3 finder.py --setup
echo
echo "Ferdig. Du kan lukke dette vinduet."
