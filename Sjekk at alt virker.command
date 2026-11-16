#!/bin/zsh
# Double-click to check that everything works and see how to fix what does not.
cd "$(dirname "$0")" || exit 1
python3 finder.py --doctor
echo
echo "Ferdig. Du kan lukke dette vinduet."
