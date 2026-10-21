"""Telegram front-end. Run: python -m bot.main"""
import asyncio
import logging
import os
import re
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from .executor import Executor, mt5
from .parser import ParseError, is_signal, parse_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()])
log = logging.getLogger("main")
for noisy in ("httpx", "apscheduler"):  # httpx logs URLs containing the bot token
    logging.getLogger(noisy).setLevel(logging.WARNING)

ALLOWED = {int(x) for x in os.environ["ALLOWED_CHAT_IDS"].split("#")[0].replace(" ", "").split(",") if x}
ex = Executor()
_last_sid = 0
_reconnected = False

# always-visible keyboard under the chat box; "🚨 EMERGENCY STOP" text is itself a stop word
PANIC = ReplyKeyboardMarkup([[KeyboardButton("🚨 EMERGENCY STOP")],
                             [KeyboardButton("💰 Balance"), KeyboardButton("📋 Positions"),
                              KeyboardButton("⚙️ Lot Size")],
                             [KeyboardButton("⏸ Pause"), KeyboardButton("▶️ Resume")]],
                            resize_keyboard=True, is_persistent=True)
KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("💰 Balance", callback_data="BAL"),
     InlineKeyboardButton("📋 Positions", callback_data="POS")],
    [InlineKeyboardButton("🚨 EMERGENCY STOP", callback_data="KILL")],
])
LOT_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton(f"{v} lot", callback_data=f"LOT:{v}") for v in ("0.01", "0.02", "0.05", "0.10")],
    [InlineKeyboardButton(f"{v} lot", callback_data=f"LOT:{v}") for v in ("0.20", "0.50", "1.00")],
    [InlineKeyboardButton(f"Auto {v}% risk", callback_data=f"RISK:{v}") for v in ("0.5", "1", "2")],
])


HALTED_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("▶️ Resume", callback_data="RESUME"),
     InlineKeyboardButton("💰 Balance", callback_data="BAL")],
])


def trade_keyboard(sid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Close this trade", callback_data=f"CG:{sid}"),
         InlineKeyboardButton("💰 Balance", callback_data="BAL")],
        [InlineKeyboardButton("📋 Positions", callback_data="POS"),
         InlineKeyboardButton("🚨 EMERGENCY STOP", callback_data="KILL")],
    ])


def positions_view():
    """Text + one close button per open position."""
    pos = ex.open_positions()
    if not pos:
        return "📋 No open positions.", KEYBOARD
    lines, rows = ["📋 OPEN POSITIONS"], []
    for p in pos:
        side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
        leg = p.comment.split("|")[1] if "|" in p.comment else ""
        lines.append(f"#{p.ticket} {side} {p.symbol} {p.volume} @ {p.price_open} "
                     f"SL {p.sl} TP {p.tp} {leg}  P/L {p.profit:+.2f}")
        rows.append([InlineKeyboardButton(f"❌ Close #{p.ticket} {side} {leg} ({p.profit:+.2f})",
                                          callback_data=f"CP:{p.ticket}")])
    lines.append(f"\nTotal P/L: {sum(p.profit for p in pos):+.2f}")
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="POS"),
                 InlineKeyboardButton("🚨 CLOSE ALL", callback_data="KILL")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


STOP_RE = re.compile(r"\b(STOP|CLOSE\s*ALL|KILL|EMERGENCY|EXIT\s*ALL)\b", re.I)


def is_stop_command(text: str) -> bool:
    # "stop", "stop the trade", "close all now"... but never a signal ("SELL STOP", "STOP LOSS")
    return bool(STOP_RE.search(text)) and not is_signal(text) and len(text) < 60


def authorized(update: Update) -> bool:
    return update.effective_user and update.effective_user.id in ALLOWED


LOST = (EOFError, ConnectionError, BrokenPipeError, TimeoutError)


async def run(fn, *a):
    """Run a blocking MT5 call. If MT5 was closed meanwhile, reconnect (reopens it) once."""
    try:
        return await asyncio.to_thread(fn, *a)
    except LOST as e:
        global _reconnected
        log.warning("MT5 connection lost (%r) — reconnecting", e)
        mt5.reset()
        _reconnected = True
        return await asyncio.to_thread(fn, *a)


async def notify(bot, text, **kw):
    for uid in ALLOWED:
        try:
            await bot.send_message(uid, text, **kw)
        except Exception:
            log.exception("notify failed")


async def connection_lost(bot, e):
    """MT5 could not be reached even after reopening it."""
    log.error("MT5 unreachable: %r", e)
    mt5.reset()
    await notify(bot, "⚠️ Could not reach MT5 — will retry automatically.\n"
                      "Open trades keep their SL/TP inside MT5. Check: systemctl --user status mt5-stack")


async def opening_notice(message):
    if not mt5.connected:
        await message.reply_text("⏳ Opening MT5… (takes ~20-30 s)")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    text = (update.message.text or update.message.caption or "").strip()
    if is_stop_command(text):
        await update.message.reply_text(await run(ex.emergency_stop), reply_markup=HALTED_KB)
        return
    low = text.lower()
    short = len(text) < 20
    if short and any(w in low for w in ("balance", "status", "position")):
        await opening_notice(update.message)
    if short and ("balance" in low or "status" in low):
        await update.message.reply_text(await run(ex.balance), reply_markup=KEYBOARD); return
    if short and "position" in low:
        t, kb = await run(positions_view)
        await update.message.reply_text(t, reply_markup=kb); return
    if short and "lot size" in low:
        await update.message.reply_text(f"⚙️ Current: {ex.lot_mode()}\nChoose lot size per TP order "
                                        "(3 orders per signal), or type e.g.  lot 0.03  /  risk 1.5",
                                        reply_markup=LOT_MENU); return
    m = re.fullmatch(r"(lot|risk)\s*([\d.]+)\s*%?", low)
    if m:
        v = float(m.group(2))
        await update.message.reply_text(ex.set_fixed_lot(v) if m.group(1) == "lot" else ex.set_risk(v)); return
    if "pause" in low and len(text) < 20:
        ex.halted = True
        await update.message.reply_text("⏸ Paused — no new trades. Open trades untouched."); return
    if "resume" in low and len(text) < 20:
        ex.halted = False
        await update.message.reply_text("▶️ Resumed — accepting signals."); return
    m = re.match(r"price\s+(\w+)\s+([\d.]+)$", text, re.I)
    if m and mt5.is_paper:  # paper mode only: move the simulated market
        mt5.set_price(m.group(1).upper(), float(m.group(2)))
        await update.message.reply_text(await run(ex.status)); return
    if not is_signal(text):
        if re.search(r"\b(BUY|SELL)\b", text, re.I) and re.search(r"\d", text):
            await update.message.reply_text("❌ Looks like a signal but has no SL — not trading it.")
        return
    try:
        sig = parse_signal(text)
    except ParseError as e:
        await update.message.reply_text(f"❌ Could not parse signal: {e}")
        return
    if ex.is_duplicate(sig):
        await update.message.reply_text(f"⚠️ Duplicate signal ignored — same {sig.side} {sig.symbol} "
                                        f"was received in the last {ex.dedupe_min} min.")
        return
    await update.message.reply_text(
        f"🔎 Parsed: {sig.side} {sig.symbol} entry {sig.entry_low}-{sig.entry_high} SL {sig.sl} TPs {sig.tps}\nAnalysing…")
    await opening_notice(update.message)
    global _last_sid
    _last_sid = max(_last_sid + 1, int(time.time()) % 10_000_000)  # unique even within one second
    sid = f"S{_last_sid}"
    try:
        msg = await run(ex.place, sig, sid)
    except LOST as e:
        msg = "❌ MT5 not reachable — signal NOT placed. Please resend in a minute."
        await connection_lost(ctx.bot, e)
    except Exception as e:
        log.exception("place failed")
        msg = f"❌ Error: {e}"
    log.info("signal %s result:\n%s", sid, msg)
    await update.message.reply_text(msg, reply_markup=trade_keyboard(sid) if "✅" in msg else KEYBOARD)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not authorized(update):
        await q.answer("Not allowed"); return
    await q.answer()
    d, kb = q.data, KEYBOARD
    if d in ("KILL", "BAL", "STATUS", "POS") or d.startswith(("CP:", "CG:")):
        await opening_notice(q.message)
    try:
        if d == "KILL":
            msg, kb = await run(ex.emergency_stop), HALTED_KB
        elif d == "RESUME":
            ex.halted = False; msg = "▶️ Resumed — accepting signals."
        elif d in ("BAL", "STATUS"):
            msg = await run(ex.balance)
        elif d == "POS":
            msg, kb = await run(positions_view)
        elif d.startswith("CP:"):
            msg = await run(ex.close_ticket, int(d[3:]))
        elif d.startswith("CG:"):
            msg = await run(ex.close_group, d[3:])
        elif d.startswith("LOT:"):
            msg = ex.set_fixed_lot(float(d[4:]))
        elif d.startswith("RISK:"):
            msg = ex.set_risk(float(d[5:]))
        else:
            return
    except LOST as e:
        await connection_lost(ctx.bot, e)
        msg = "❌ MT5 not reachable — try again in a minute." + (" CHECK MT5 MANUALLY!" if d == "KILL" else "")
    except Exception as e:
        log.exception("button %s", d)
        msg = f"❌ Error: {e}" + (" — CLOSE MANUALLY IN MT5!" if d == "KILL" else "")
    log.info("button %s -> %s", d, msg.splitlines()[0])
    await q.message.reply_text(msg, reply_markup=kb)


async def cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    c = update.message.text.split()[0].lstrip("/").split("@")[0].lower()
    if c in ("stop", "kill"):
        await update.message.reply_text(await run(ex.emergency_stop), reply_markup=HALTED_KB); return
    elif c == "resume":
        ex.halted = False; msg = "▶️ Resumed."
    elif c == "pause":
        ex.halted = True; msg = "⏸ Paused — no new trades (open trades untouched). Use 🚨 to close them."
    elif c in ("status", "balance"):
        msg = await run(ex.balance)
    elif c == "positions":
        msg, kb = await run(positions_view)
        await update.message.reply_text(msg, reply_markup=kb); return
    elif c == "lot":
        await update.message.reply_text(f"⚙️ Current: {ex.lot_mode()}", reply_markup=LOT_MENU); return
    else:
        msg = ("🤖 Signal bot ready — " + ex.lot_mode() + "\n\n"
               "Send a signal to trade. Buttons below:\n"
               "💰 Balance · 📋 Positions (close any trade) · ⚙️ Lot Size\n"
               "🚨 EMERGENCY STOP closes everything (or type 'stop').\n"
               "Type  lot 0.05  or  risk 1  to change size.")
        await update.message.reply_text(msg, reply_markup=PANIC); return
    await update.message.reply_text(msg, reply_markup=KEYBOARD)


async def manage_job(ctx: ContextTypes.DEFAULT_TYPE):
    global _reconnected
    had_trades, _reconnected = bool(ex.groups), False
    try:
        msgs = await run(ex.manage)
    except LOST as e:
        await connection_lost(ctx.bot, e); return
    except Exception:
        log.exception("manage loop"); return
    if had_trades and _reconnected:
        msgs.insert(0, "ℹ️ MT5 was closed while trades are open — reopened it to manage them.")
    for m in msgs:
        log.info("manage: %s", m)
        await notify(ctx.bot, m)


async def on_startup(app):
    state = "⛔ STOPPED (tap ▶️ Resume to trade)" if ex.halted else "🟢 RUNNING — accepting signals"
    if ex.groups or mt5.is_paper:
        label = await run(ex.account_label)  # open trades to manage -> MT5 needed now
    else:
        if sys.platform != "win32":
            await run(mt5.close_terminal)  # leftover MT5 from before a restart
        label = "MT5 stays closed until a signal arrives (opens automatically)."
    await notify(app.bot, f"🤖 Bot started\n{label}\nLot mode: {ex.lot_mode()}\n{state}",
                 reply_markup=PANIC)


def main():
    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).post_init(on_startup).build()
    app.add_handler(CommandHandler(["start", "help", "stop", "kill", "resume", "pause", "status", "balance", "positions", "lot"], cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, on_text))
    app.job_queue.run_repeating(manage_job, interval=5, first=5)
    log.info("bot running")
    app.run_polling()


if __name__ == "__main__":
    main()
