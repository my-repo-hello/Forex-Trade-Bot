#!/usr/bin/env bash
# Supervises MT5 (Wine) + the rpyc bridge as ONE unit, because both share a
# wineserver: restarting one separately breaks the other's sockets.
#  1. wait until the desktop (X display) is usable
#  2. open MT5 if it is not already running
#  3. run the bridge
#  4. every 15 s check MT5 + bridge port:
#     - MT5 closed (by you or by the bot when idle) -> stop cleanly, do NOT reopen
#       (the bot reopens it on demand: signal, button, or open trades to manage)
#     - bridge died while MT5 runs -> exit 1 -> systemd restarts the stack
set -u
export WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}" WINEDEBUG=-all
export DISPLAY="${DISPLAY:-:0}" XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
DIR="$(cd "$(dirname "$0")" && pwd)"
TERMINAL="$WINEPREFIX/drive_c/Program Files/MetaTrader 5/terminal64.exe"
WINPY="C:\\users\\$USER\\AppData\\Local\\Programs\\Python\\Python311\\python.exe"
PORT=18812

log() { echo "[mt5-stack] $*"; }
port_up() { ss -ltn 2>/dev/null | grep -q "127.0.0.1:$PORT "; }
mt5_up() { pgrep -f 'terminal64\.exe' >/dev/null; }

log "waiting for desktop session on $DISPLAY ..."
until xset q >/dev/null 2>&1; do sleep 5; done

if ! mt5_up; then
    log "starting MetaTrader 5"
    wine "$TERMINAL" >/dev/null 2>&1 &
fi
for _ in $(seq 60); do mt5_up && break; sleep 2; done
mt5_up || { log "MT5 did not start"; exit 1; }
log "MT5 running"
sleep 10  # let the terminal finish logging in

log "starting bridge"
wine "$WINPY" "Z:${DIR//\//\\}\\mt5_bridge_server.py" &
for _ in $(seq 30); do port_up && break; sleep 2; done
port_up || { log "bridge port $PORT never opened"; exit 1; }
log "bridge listening on 127.0.0.1:$PORT — stack healthy"

fails=0
while true; do
    sleep 15
    if ! mt5_up; then
        log "MT5 was closed — stopping (bot reopens it when needed)"; exit 0
    fi
    if port_up; then
        fails=0
    else
        fails=$((fails + 1))
        log "bridge health check failed ($fails/3)"
        [ "$fails" -ge 3 ] && { log "restarting stack"; exit 1; }
    fi
done
