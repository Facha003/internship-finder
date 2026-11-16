#!/bin/zsh
# Double-click this file in Finder to search and open the results page.
cd "$(dirname "$0")" || exit 1
python3 finder.py
echo
echo "Done. You can close this window."
