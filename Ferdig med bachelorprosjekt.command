#!/bin/zsh
# Double-click when you have found a bachelor project: switches the tool to internships only. Reversible via Oppsett.command.
cd "$(dirname "$0")" || exit 1
python3 finder.py --retire-bachelor
echo
echo "Ferdig. Du kan lukke dette vinduet."
