# Forex Signal Auto-Trader (Telegram → MT5)

📘 **[Complete setup & user guide → docs/GUIDE.md](docs/GUIDE.md)**

Send a signal to your Telegram bot → it parses it, checks the live price, sizes the lot by risk %,
and places 3 orders (one per TP) in MT5. Type **stop** or tap **🚨 EMERGENCY STOP** to close everything immediately.

```
GOLD SELL 4503-4504          GOLD SELL LIMIT 4142-4143
SL 4520.00                   SL 4151.00
TP1 4492.00  TP2 4479.00  TP3 4465.00
```

## Setup
1. Create a bot with **@BotFather** → copy the token. Get your user id from **@userinfobot**.
2. `cp .env.example .env` and fill it in. **Use a DEMO account first.**
3. Install + run (see your OS below), then send `/start` to your bot.

### Windows
1. Install MT5 from your broker, log in, enable **Tools → Options → Expert Advisors → Allow algorithmic trading**.
2. Install Python 3.11+ (64-bit), then:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python -m bot.main
   ```

### Linux (MT5 runs under Wine)
Full steps in **[docs/GUIDE.md](docs/GUIDE.md#15b-install-on-linux-mt5-runs-inside-wine)**. Short version:
MT5 + Windows Python (with `MetaTrader5`, `rpyc`) in the `~/.mt5` Wine prefix, then
`./deploy/install_services.sh` starts MT5 → bridge → bot as auto-restarting services.

### Paper mode (any OS, no broker)
Set `MT5_MODE=paper`. Simulate the market by sending `price XAUUSD 4503.5` in Telegram.

## How it decides the entry (price same / better / worse)
| Live price vs entry zone | Action |
|---|---|
| Inside zone | Market order |
| Better than zone (lower for BUY, higher for SELL) | Market order |
| Worse, but within `ENTRY_TOLERANCE_POINTS` | Market order |
| Worse beyond tolerance | LIMIT order at zone edge, cancelled after `PENDING_EXPIRY_MIN` |
| Already past TP1 or SL | Skipped |

Also skipped: no SL, SL/TP on the wrong side, spread > `MAX_SPREAD_POINTS`, `MAX_OPEN_SIGNALS` reached.

## Trade management
- 3 positions, one per TP, total risk = `RISK_PERCENT` of balance.
- TP1 hit → SL to break-even. TP2 hit → last leg SL locked at TP1.

## Controls
| Send | Effect |
|---|---|
| 🚨 EMERGENCY STOP / `stop` / `close all` / `/stop` | Close all bot positions, cancel pendings, halt |
| ⏸ Pause / `/pause` | No new trades (open trades still managed) |
| ▶️ Resume / `/resume` | Accept signals again |
| 💰 Balance / `/balance` | Balance, equity, open trades |
| 📋 Positions | Each trade with its own ❌ Close button |
| ⚙️ Lot Size / `lot 0.05` / `risk 1` | Fixed lot or auto risk % |

Only Telegram user ids in `ALLOWED_CHAT_IDS` are obeyed. Only trades with the bot's `MAGIC` number are touched.

## Run 24/7 (Linux auto-start)
`./deploy/install_services.sh` installs 3 systemd user services — `mt5-terminal`, `mt5-bridge`,
`forex-bot` — that start at boot and restart automatically on crash. If MT5 or the bridge drops,
the bot warns you in Telegram and restarts itself (~40 s). Open trades keep their SL/TP inside MT5.
```
systemctl --user status forex-bot          # state
journalctl --user -u forex-bot -f          # live log
systemctl --user restart forex-bot         # after editing .env
```

## Safety features
- Emergency stop / pause **survive restarts** (bot stays stopped until ▶️ Resume)
- `DAILY_LOSS_LIMIT_PCT` — no new trades after that % loss today
- Duplicate signal (same levels within `DUPLICATE_WINDOW_MIN`) ignored
- Requote / price-changed retried with fresh price
- Broker symbol auto-detected (XAUUSD, XAUUSD+, XAUUSD.r …)
- Break-even only if the TP1 order really existed
- Startup message says **DEMO** or **⚠️ REAL MONEY** and warns if Algo Trading is off
- Result (P/L) message when a signal finishes at TP/SL

## Switching to a REAL account
1. In MT5 log into the live account (File → Login), tick "save password".
2. In `.env` set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` to the live account.
3. Set a small fixed lot in Telegram first: ⚙️ Lot Size → 0.01.
4. `systemctl --user restart forex-bot` → Telegram shows **⚠️ REAL MONEY account …**.

## Tests
`MT5_MODE=paper python -m pytest -q tests`

> ⚠️ Trading is risky. The bot executes signals; it does not judge whether they are good. Test on demo for weeks first.
