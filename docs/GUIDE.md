# Setup & User Guide — Telegram → MT5 Signal Bot

Paste a trading signal into your Telegram bot → it checks the live price, sizes the lot,
and places the trade in MetaTrader 5. Buttons in Telegram let you see balance, change
lot size, close trades, and hit an **🚨 EMERGENCY STOP**.

> ⚠️ Trading carries real risk. The bot executes signals exactly; it cannot tell a good
> signal from a bad one. Always test on a **demo** account first and start with **0.01 lot**.

---

## Part 1 — Setup

### 1.1 What you need
| Item | Notes |
|---|---|
| MT5 account | From your broker (e.g. Vantage). Start with **demo**. |
| Telegram account | On phone or desktop |
| A PC that stays on | Linux (Wine) or Windows. A VPS is best for 24/7. |
| Python 3.11+ | Linux: system Python. Windows: python.org 64-bit installer |

### 1.2 Create the Telegram bot
1. In Telegram open **@BotFather** → `/newbot` → choose a name and username.
2. Copy the **token** it gives you (looks like `123456:ABC...`). Keep it secret.
3. Open your new bot and press **Start**, then send `hi`.
4. Get your numeric user id from **@userinfobot** (e.g. `123456789`).

### 1.3 Download the code
```bash
git clone git@github.com:my-repo-hello/Forex-Trade-Bot.git
cd Forex-Trade-Bot
cp .env.example .env
```

### 1.4 Fill in `.env`
| Key | What to put |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `ALLOWED_CHAT_IDS` | Your user id (only these users can control the bot) |
| `MT5_PATH` | `C:\Program Files\MetaTrader 5\terminal64.exe` (default) |
| `MT5_LOGIN` / `MT5_PASSWORD` / `MT5_SERVER` | Your MT5 account, server exactly as shown in MT5 |
| `MT5_MODE` | `auto` (real MT5) or `paper` (simulator, no broker) |
| `RISK_PERCENT` | Auto lot sizing: % of balance risked per signal |
| `MAX_LOT` | Upper cap per order |
| `MAX_OPEN_SIGNALS` | Max signals open at once (use 1–2 when starting) |
| `MAX_SPREAD_POINTS` | Skip when spread is wider (gold: 50 = $0.50) |
| `ENTRY_TOLERANCE_POINTS` | How far price may be past entry and still enter at market |
| `PENDING_EXPIRY_MIN` | Cancel unfilled limit orders after N minutes |
| `DAILY_LOSS_LIMIT_PCT` | Stop new trades after this % loss today |
| `DUPLICATE_WINDOW_MIN` | Ignore the same signal repeated within N minutes |
| `SYMBOL_SUFFIX` | Usually leave empty — the bot auto-detects `XAUUSD+`, `XAUUSD.r` … |

Never commit or share `.env`.

### 1.5a Install on **Windows**
1. Install MT5 from your broker, log in, tick **save password**.
2. In MT5 press the **Algo Trading** button (must be green).
   Also *Tools → Options → Expert Advisors → Allow algorithmic trading*.
3. In a terminal inside the project folder:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python -m bot.main
   ```
4. For 24/7: create a Task Scheduler task "At log on" running
   `C:\path\Forex-Trade-Bot\.venv\Scripts\python.exe -m bot.main` with *Start in* = the project folder,
   and set MT5 to start with Windows.

### 1.5b Install on **Linux** (MT5 runs inside Wine)
```bash
# 1. Wine + MT5 (MetaQuotes' official installer uses the ~/.mt5 prefix)
sudo apt install wine64
#    install MT5 with Wine, log in, tick "save password", turn Algo Trading on

# 2. Windows Python inside the same Wine prefix (from python.org)
WINEPREFIX=~/.mt5 wine python-3.11.9-amd64.exe /quiet InstallAllUsers=0 PrependPath=1
WINEPREFIX=~/.mt5 wine python -m pip install MetaTrader5 rpyc==6.0.2

# 3. Linux side
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Start everything as auto-start services (MT5 -> bridge -> bot)
./deploy/install_services.sh
```
How it works: the `mt5-stack` service (`bridge/mt5_stack.sh`) waits for your desktop, **opens MT5
automatically**, then runs a small bridge server inside Wine (127.0.0.1:18812 only)
that gives the Linux bot access to the official `MetaTrader5` package.

Useful commands:
```bash
systemctl --user status mt5-stack forex-bot   # is it running?
journalctl --user -u forex-bot -f        # live log
systemctl --user restart forex-bot       # after editing .env
systemctl --user stop forex-bot          # stop the bot (MT5 keeps SL/TP)
```

### 1.6 First start check
Within ~30 s Telegram shows:
```
🤖 Bot started
DEMO account 12345678 (YourBroker-Demo)
Lot mode: FIXED 0.01 lot/TP
🟢 RUNNING — accepting signals
```
- **DEMO** vs **⚠️ REAL MONEY** tells you which account is live.
- If it says *Algo Trading is DISABLED*, press the Algo Trading button in MT5.

### 1.7 Try without a broker (paper mode)
Set `MT5_MODE=paper`, start the bot, then in Telegram send `price XAUUSD 4503.5`
to move the simulated market, and paste a signal.

---

## Part 2 — How to use

### 2.1 Sending a signal
Paste or forward a signal to the bot. These formats work:
```
GOLD SELL 4503-4504          GOLD SELL LIMIT 4142-4143       XAUUSD BUY NOW @ 4500
SL 4520.00                   SL 4151.00                      SL: 4490
TP1 4492.00                  TP1 4135.00                     TP 4510
TP2 4479.00                  TP2 4127.00                     TP 4520
TP3 4465.00                  TP3 4120.00
```
- Symbols: GOLD/XAU → XAUUSD, SILVER, EURUSD, EUR/USD, US30, NAS100, BTC …
- **No SL = no trade.** SL/TP on the wrong side of entry = rejected.
- Extra words ("copy", "Free Signal", emojis) are ignored.

### 2.2 What the bot does with the price
| Live price vs entry zone | Action |
|---|---|
| Inside the zone | Market order |
| Better (lower for BUY, higher for SELL) | Market order |
| Slightly worse, within tolerance | Market order |
| Far worse | LIMIT order at the zone (expires after `PENDING_EXPIRY_MIN`) |
| Already past TP1 or SL | Skipped — "missed" |

Also skipped: spread too wide, max open signals reached, daily loss limit hit,
duplicate signal, bot stopped.

### 2.3 Confirmation message
```
✅ TRADE PLACED SUCCESSFULLY
SELL XAUUSD | MARKET @ 4117.59
Lot: 0.01 x 3 = 0.03
SL: 4127.0 (risk ≈ $28.23)
✅ TP1 4110.0 — ticket #2212653041
✅ TP2 4103.0 — ticket #2212653056
✅ TP3 4096.0 — ticket #2212653067
💰 Balance: 99,326.73 | Lot mode: FIXED 0.01 lot/TP
[❌ Close this trade] [💰 Balance] [📋 Positions] [🚨 EMERGENCY STOP]
```
Other headers: `✅ PENDING ORDER PLACED` (waiting for price), `⚠️ PARTIALLY PLACED`,
`❌ TRADE FAILED` (with the broker's reason).

### 2.4 Automatic trade management
Each signal opens **3 orders** (one per TP):
- **TP1 hit** → SL of the other two moves to break-even → "✅ TP1 hit → SL moved to break-even"
- **TP2 hit** → last order's SL moves to TP1 (profit locked)
- **Pending filled** → "✅ TRADE OPENED SUCCESSFULLY"
- **All closed** → "🏁 🟢/🔴 Signal CLOSED — result +12.40"

### 2.5 Buttons (always under the chat box)
| Button / text | Action |
|---|---|
| 🚨 **EMERGENCY STOP** (or type `stop`, `close all`, `/stop`) | Closes ALL bot positions, cancels pendings, halts. Shows P/L + balance and a ▶️ Resume button |
| 💰 **Balance** | Balance, equity, floating P/L, free margin, lot mode, running/stopped |
| 📋 **Positions** | Every open trade with its own **❌ Close #ticket** button, 🔄 Refresh, 🚨 Close all |
| ⚙️ **Lot Size** | Fixed 0.01 – 1.00 lot per order, or Auto 0.5% / 1% / 2% risk |
| ⏸ **Pause** | No new trades; open trades still managed |
| ▶️ **Resume** | Accept signals again (also after emergency stop) |

Typed shortcuts: `lot 0.03`, `risk 1.5`, `balance`, `positions`, `/lot`.

**Lot size note:** fixed lot is *per order*; a signal places 3 orders, so `0.01` = 0.03 lot total.
Stopped/paused state and lot setting are remembered after restarts.

### 2.6 Daily routine
1. Check Telegram shows the startup message / 💰 Balance says 🟢 RUNNING.
2. Paste signals as they come.
3. Glance at 📋 Positions; use 🚨 if anything looks wrong.

---

## Part 3 — Going live (real money)
1. Run 1–2 weeks on demo with the channel's real signals; check the results.
2. MT5: *File → Login to Trade Account* → live account, save password.
3. `.env`: set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` to the live account
   (otherwise the bot logs MT5 back into demo). Set `MAX_OPEN_SIGNALS=1`.
4. Telegram ⚙️ Lot Size → **0.01**.
5. Restart: `systemctl --user restart forex-bot` (Linux) or restart the script (Windows).
6. Telegram must say **⚠️ REAL MONEY account …** before you send signals.

---

## Part 4 — Troubleshooting
| Symptom | Fix |
|---|---|
| Bot doesn't answer | Is it running? `systemctl --user status forex-bot`. Is your id in `ALLOWED_CHAT_IDS`? |
| `MT5 bridge not reachable` | `systemctl --user restart mt5-stack` (reopens MT5 + bridge) |
| MT5 doesn't open after boot | It opens after you log in to the desktop; check `journalctl --user -u mt5-stack` |
| `Algo Trading is DISABLED` | Press the Algo Trading button in MT5 |
| `Unsupported filling mode` / `Invalid stops` | Broker rule — report the message; SL may be too close |
| `symbol ... not available` | Check the symbol name in MT5 Market Watch; set `SYMBOL_SUFFIX` |
| `Spread ... > max` | Wait for normal spread, or raise `MAX_SPREAD_POINTS` |
| `DAILY LOSS LIMIT hit` | Protection; resets next day (or raise `DAILY_LOSS_LIMIT_PCT`) |
| "Lost connection to MT5 — restarting" | Automatic recovery; if it repeats, check MT5 is logged in |
| Bot token leaked | @BotFather → `/revoke`, put the new token in `.env`, restart |

## Tests
```bash
MT5_MODE=paper .venv/bin/python -m pytest -q tests
```
