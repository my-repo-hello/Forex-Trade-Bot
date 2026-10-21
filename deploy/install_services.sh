#!/usr/bin/env bash
# Install auto-start (systemd --user) services:
#   mt5-stack  : opens MT5 (Wine) + runs the bridge, health-checked as one unit
#   forex-bot  : the Telegram bot
# Both start automatically after login/boot and restart on crash.
#   ./deploy/install_services.sh          install + start
#   systemctl --user status mt5-stack forex-bot
#   journalctl --user -u forex-bot -f     live logs
set -euo pipefail
BOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${WINEPREFIX:-$HOME/.mt5}"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

# remove the old split services (MT5 and bridge must not be restarted separately)
for old in mt5-terminal mt5-bridge; do
    systemctl --user disable --now "$old" 2>/dev/null || true
    rm -f "$UNIT_DIR/$old.service"
done

cat > "$UNIT_DIR/mt5-stack.service" <<UNIT
[Unit]
Description=MetaTrader 5 (Wine) + Linux bridge, supervised together

[Service]
Environment=WINEPREFIX=$PREFIX DISPLAY=${DISPLAY:-:0} XAUTHORITY=$HOME/.Xauthority
ExecStart=$BOT_DIR/bridge/mt5_stack.sh
# stop every Wine process cleanly so the next start is fresh
ExecStopPost=/usr/bin/env WINEPREFIX=$PREFIX wineserver -k
KillMode=control-group
TimeoutStopSec=30
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT

cat > "$UNIT_DIR/forex-bot.service" <<UNIT
[Unit]
Description=Telegram -> MT5 signal bot
After=mt5-stack.service network-online.target
Wants=mt5-stack.service

[Service]
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable mt5-stack forex-bot
systemctl --user restart mt5-stack forex-bot
echo "Installed. Status: systemctl --user status mt5-stack forex-bot"
