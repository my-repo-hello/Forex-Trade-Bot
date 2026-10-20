#!/usr/bin/env bash
# Install auto-start (systemd --user) services: MT5 terminal -> bridge -> Telegram bot.
# All restart automatically on crash and start after reboot.
#   ./deploy/install_services.sh          install + start
#   systemctl --user status forex-bot     check
#   journalctl --user -u forex-bot -f     live logs
set -euo pipefail
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${WINEPREFIX:-$HOME/.mt5}"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/mt5-terminal.service" <<UNIT
[Unit]
Description=MetaTrader 5 terminal (Wine)

[Service]
Environment=WINEPREFIX=$PREFIX WINEDEBUG=-all DISPLAY=${DISPLAY:-:0}
ExecStart=/usr/bin/wine "$PREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
UNIT

cat > "$UNIT_DIR/mt5-bridge.service" <<UNIT
[Unit]
Description=MT5 <-> Linux rpyc bridge (localhost only)
After=mt5-terminal.service
Wants=mt5-terminal.service

[Service]
Environment=WINEPREFIX=$PREFIX DISPLAY=${DISPLAY:-:0}
ExecStart=$BOT_DIR/bridge/start_bridge.sh
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT

cat > "$UNIT_DIR/forex-bot.service" <<UNIT
[Unit]
Description=Telegram -> MT5 signal bot
After=mt5-bridge.service network-online.target
Wants=mt5-bridge.service

[Service]
WorkingDirectory=$BOT_DIR
ExecStartPre=/bin/sleep 20
ExecStart=$BOT_DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable mt5-terminal mt5-bridge forex-bot
systemctl --user restart mt5-terminal mt5-bridge forex-bot
echo "Installed. Status: systemctl --user status forex-bot"
