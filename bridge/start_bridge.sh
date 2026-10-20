#!/usr/bin/env bash
# Start the MT5 <-> Linux bridge (Wine). Keep this running while the bot runs.
export WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}" WINEDEBUG=-all
DIR="$(cd "$(dirname "$0")" && pwd)"
exec wine "C:\\users\\$USER\\AppData\\Local\\Programs\\Python\\Python311\\python.exe" "Z:${DIR//\//\\}\\mt5_bridge_server.py"
