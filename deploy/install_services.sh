#!/usr/bin/env bash
# Install systemd --user services:
#   forex-bot  : the Telegram bot — starts at boot/login, restarts on crash
#   mt5-stack  : MT5 (Wine) + bridge, health-checked as one unit. NOT started at boot:
#                the bot starts it only when needed (signal, button, open trades)
#                and stops it again after MT5_IDLE_CLOSE_MIN minutes with no trades.
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
# Wine ignores SIGTERM: shut the whole Wine session down directly (fast, no leftovers)
ExecStop=/usr/bin/env WINEPREFIX=$PREFIX wineserver -k
ExecStopPost=/usr/bin/env WINEPREFIX=$PREFIX wineserver -k
KillMode=control-group
SuccessExitStatus=1 143 SIGTERM SIGKILL
TimeoutStopSec=20
Restart=on-failure
RestartSec=10
UNIT

cat > "$UNIT_DIR/forex-bot.service" <<UNIT
[Unit]
Description=Telegram -> MT5 signal bot
After=network-online.target

[Service]
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python -m bot.main
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user disable mt5-stack 2>/dev/null || true   # on-demand only
systemctl --user enable forex-bot
systemctl --user restart forex-bot
echo "Installed. MT5 opens when a signal arrives. Status: systemctl --user status forex-bot mt5-stack"
