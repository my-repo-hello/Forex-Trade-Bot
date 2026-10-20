"""MT5 execution layer: connect, analyse price vs signal, size, place, manage, kill."""
import datetime
import hashlib
import json
import logging
import math
import os
import threading
import time

from .mt5_api import mt5
from .parser import Signal

log = logging.getLogger("executor")
E = os.environ


def cfg(key, default, cast=str):
    v = E.get(key, "").split("#")[0].strip()
    return cast(v) if v else default


class Executor:
    def __init__(self):
        self.magic = cfg("MAGIC", 777001, int)
        self.risk_pct = cfg("RISK_PERCENT", 1.0, float)
        self.max_lot = cfg("MAX_LOT", 1.0, float)
        self.max_spread = cfg("MAX_SPREAD_POINTS", 50, int)
        self.tolerance = cfg("ENTRY_TOLERANCE_POINTS", 300, int)
        self.max_signals = cfg("MAX_OPEN_SIGNALS", 3, int)
        self.suffix = cfg("SYMBOL_SUFFIX", "")
        self.be_after_tp1 = cfg("MOVE_SL_TO_BE_AFTER_TP1", "true").lower() == "true"
        self.expiry_min = cfg("PENDING_EXPIRY_MIN", 120, int)
        self.lock_after_tp2 = cfg("LOCK_TP1_AFTER_TP2", "true").lower() == "true"
        self.daily_loss_pct = cfg("DAILY_LOSS_LIMIT_PCT", 5.0, float)
        self.dedupe_min = cfg("DUPLICATE_WINDOW_MIN", 30, int)
        self._halted = False         # set by STOP / emergency button; persisted
        self.day = {"date": "", "balance": 0.0}
        self.recent = {}             # signal fingerprint -> time, blocks duplicates
        self.lock = threading.Lock()
        self.state_file = cfg("STATE_FILE", "state.json")
        self.groups = self._load_groups()
        self.fixed_lot = None        # None -> size by RISK_PERCENT; set from Telegram
        self.settings_file = cfg("SETTINGS_FILE", "settings.json")
        self._load_settings()

    # ---------- lot settings (changed from Telegram, survive restarts) ----------
    def _load_settings(self):
        try:
            with open(self.settings_file) as f:
                d = json.load(f)
            self.fixed_lot, self.risk_pct = d.get("fixed_lot"), d.get("risk_pct", self.risk_pct)
            self._halted = d.get("halted", False)
            self.day = d.get("day", self.day)
        except (OSError, ValueError):
            pass

    def _save_settings(self):
        tmp = self.settings_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"fixed_lot": self.fixed_lot, "risk_pct": self.risk_pct,
                       "halted": self._halted, "day": self.day}, f)
        os.replace(tmp, self.settings_file)

    @property
    def halted(self):
        return self._halted

    @halted.setter
    def halted(self, v):  # survives restarts: after 🚨 the bot stays stopped until Resume
        self._halted = bool(v)
        self._save_settings()

    # ---------- daily loss limit ----------
    def daily_block(self):
        """Return a reason string if today's loss limit is hit, else None."""
        a = mt5.account_info()
        today = datetime.date.today().isoformat()
        if self.day.get("date") != today:
            self.day = {"date": today, "balance": a.balance}
            self._save_settings()
        start = self.day["balance"] or a.balance
        loss_pct = (start - a.equity) / start * 100 if start else 0
        if self.daily_loss_pct and loss_pct >= self.daily_loss_pct:
            return (f"🛑 DAILY LOSS LIMIT hit: -{loss_pct:.2f}% today "
                    f"(limit {self.daily_loss_pct}%). No new trades until tomorrow.")
        return None

    def is_duplicate(self, sig):
        key = hashlib.md5(repr((sig.symbol, sig.side, sig.entry_low, sig.entry_high,
                                sig.sl, tuple(sig.tps))).encode()).hexdigest()
        now = time.time()
        self.recent = {k: t for k, t in self.recent.items() if now - t < self.dedupe_min * 60}
        if key in self.recent:
            return True
        self.recent[key] = now
        return False

    def set_fixed_lot(self, lot):
        if not 0 < lot <= self.max_lot:
            return f"❌ Lot must be between 0.01 and MAX_LOT {self.max_lot}"
        self.fixed_lot = round(lot, 2); self._save_settings()
        return f"✅ Lot size set: FIXED {self.fixed_lot} lot per TP order (x3 per signal)"

    def set_risk(self, pct):
        if not 0 < pct <= 5:
            return "❌ Risk must be between 0.1% and 5%"
        self.fixed_lot, self.risk_pct = None, pct; self._save_settings()
        return f"✅ Lot size set: AUTO by risk {pct}% of balance per signal"

    def lot_mode(self):
        return f"FIXED {self.fixed_lot} lot/TP" if self.fixed_lot else f"AUTO {self.risk_pct}% risk"

    def _load_groups(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_groups(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.groups, f)
        os.replace(tmp, self.state_file)

    # ---------- connection ----------
    def connect(self):
        ok = mt5.initialize(path=cfg("MT5_PATH", None) or None,
                            login=cfg("MT5_LOGIN", 0, int),
                            password=cfg("MT5_PASSWORD", ""),
                            server=cfg("MT5_SERVER", ""))
        if not ok:
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
        acc = mt5.account_info()
        log.info("MT5 connected: %s %s balance=%.2f", acc.login, acc.server, acc.balance)
        return acc

    def account_label(self):
        a = mt5.account_info()
        kind = {0: "DEMO", 1: "CONTEST", 2: "⚠️ REAL MONEY"}.get(a.trade_mode, str(a.trade_mode))
        algo = mt5.terminal_info().trade_allowed
        return (f"{kind} account {a.login} ({a.server})\nBalance {a.balance:,.2f} {a.currency}"
                + ("" if algo else "\n❌ Algo Trading is DISABLED in MT5 — enable the 'Algo Trading' button!"))

    def ensure(self):
        if mt5.terminal_info() is None:
            self.connect()

    # ---------- helpers ----------
    def _sym(self, s):
        """XAUUSD -> broker name (XAUUSD, XAUUSD+, XAUUSD.r, XAUUSDm ...)."""
        name = s + self.suffix
        if not mt5.symbol_select(name, True):
            cands = [x.name for x in mt5.symbols_get() or []
                     if x.name.upper().startswith(s) and len(x.name) <= len(s) + 4]
            cands.sort(key=len)
            for c in cands:
                if mt5.symbol_select(c, True) and mt5.symbol_info(c).trade_mode != 0:
                    name = c; break
            else:
                raise RuntimeError(f"symbol {s} not available at this broker")
        info = mt5.symbol_info(name)
        if info.trade_mode == 0:
            raise RuntimeError(f"trading disabled for {name}")
        return name, info

    def _lot_for_risk(self, info, entry, sl, parts):
        if self.fixed_lot:
            lot = math.floor(self.fixed_lot / info.volume_step + 1e-9) * info.volume_step
            return round(max(info.volume_min, min(info.volume_max, lot)), 2)
        bal = mt5.account_info().balance
        risk_money = bal * self.risk_pct / 100
        ticks = abs(entry - sl) / info.trade_tick_size
        loss_per_lot = ticks * info.trade_tick_value
        if loss_per_lot <= 0:
            return info.volume_min
        lot = risk_money / loss_per_lot / parts
        step = info.volume_step
        lot = math.floor(lot / step + 1e-9) * step   # round DOWN so risk is never exceeded
        if lot < info.volume_min:
            log.warning("risk-based lot %.4f < broker min %.2f; using min lot", lot, info.volume_min)
        lot = max(info.volume_min, min(self.max_lot, info.volume_max, lot))
        return round(lot, 2)

    def _filling(self, info):
        fm = info.filling_mode
        if fm & 1: return mt5.ORDER_FILLING_FOK
        if fm & 2: return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def open_signal_count(self):
        pos = mt5.positions_get() or []
        ords = mt5.orders_get() or []
        tags = {p.comment for p in pos if p.magic == self.magic} | {o.comment for o in ords if o.magic == self.magic}
        return len({t.split("|")[0] for t in tags})

    # ---------- analysis + placement ----------
    def decide(self, sig: Signal, info, tick):
        """Return (order_type, price, reason). Handles price same / better / worse than entry."""
        buy = sig.side == "BUY"
        mkt = tick.ask if buy else tick.bid
        pt = info.point
        if sig.entry is None or sig.order_hint == "NOW":
            return ("MARKET", mkt, "no entry given / NOW → market")

        lo, hi = sig.entry_low, sig.entry_high
        tol = self.tolerance * pt
        # SL already broken → never enter
        if (buy and mkt <= sig.sl) or (not buy and mkt >= sig.sl):
            return (None, None, "price already beyond SL — signal invalid")
        # TP1 already hit → too late
        if (buy and mkt >= sig.tps[0]) or (not buy and mkt <= sig.tps[0]):
            return (None, None, "price already at/after TP1 — missed")

        if lo <= mkt <= hi:
            return ("MARKET", mkt, "price inside entry zone")
        if buy:
            if mkt < lo:   # cheaper than entry: better price
                return ("MARKET", mkt, "price better than entry (lower) → market")
            if mkt - hi <= tol:
                return ("MARKET", mkt, f"price {mkt} slightly above entry, within tolerance")
            return ("LIMIT", hi, "price ran above entry → BUY LIMIT at entry")
        else:
            if mkt > hi:
                return ("MARKET", mkt, "price better than entry (higher) → market")
            if lo - mkt <= tol:
                return ("MARKET", mkt, f"price {mkt} slightly below entry, within tolerance")
            return ("LIMIT", lo, "price ran below entry → SELL LIMIT at entry")

    def place(self, sig: Signal, sid: str):
        with self.lock:
            if self.halted:
                return "⛔ Bot is STOPPED. Send /resume first."
            self.ensure()
            block = self.daily_block()
            if block:
                return block
            if self.open_signal_count() >= self.max_signals:
                return f"⚠️ Max open signals ({self.max_signals}) reached — skipped."
            name, info = self._sym(sig.symbol)
            tick = mt5.symbol_info_tick(name)
            spread = (tick.ask - tick.bid) / info.point
            if spread > self.max_spread:
                return f"⚠️ Spread {spread:.0f} pts > max {self.max_spread} — skipped."

            otype, price, reason = self.decide(sig, info, tick)
            if otype is None:
                return f"❌ Not placed: {reason}"

            buy = sig.side == "BUY"
            lot = self._lot_for_risk(info, price, sig.sl, len(sig.tps))
            results, n_ok, legs = [], 0, []
            for i, tp in enumerate(sig.tps, 1):
                req = {
                    "symbol": name, "volume": lot, "sl": sig.sl, "tp": tp,
                    "magic": self.magic, "comment": f"{sid}|TP{i}",
                    "type_time": mt5.ORDER_TIME_GTC, "type_filling": self._filling(info),
                    "deviation": 30,
                }
                if otype == "MARKET":
                    t = mt5.symbol_info_tick(name)
                    req.update(action=mt5.TRADE_ACTION_DEAL, price=t.ask if buy else t.bid,
                               type=mt5.ORDER_TYPE_BUY if buy else mt5.ORDER_TYPE_SELL)
                else:
                    req.update(action=mt5.TRADE_ACTION_PENDING, price=price,
                               type=mt5.ORDER_TYPE_BUY_LIMIT if buy else mt5.ORDER_TYPE_SELL_LIMIT,
                               type_time=mt5.ORDER_TIME_SPECIFIED,
                               expiration=int(time.time()) + self.expiry_min * 60)
                r = mt5.order_send(req)
                for _ in range(2):  # requote / price changed / off quotes: retry with fresh price
                    if otype != "MARKET" or r is None or r.retcode not in (10004, 10020, 10021):
                        break
                    time.sleep(0.3)
                    t = mt5.symbol_info_tick(name)
                    req["price"] = t.ask if buy else t.bid
                    r = mt5.order_send(req)
                if otype != "MARKET" and (r is None or r.retcode != mt5.TRADE_RETCODE_PLACED):
                    # some brokers reject ORDER_TIME_SPECIFIED -> retry GTC, manager expires it
                    req["type_time"] = mt5.ORDER_TIME_GTC; req.pop("expiration", None)
                    r = mt5.order_send(req)
                ok = r is not None and r.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
                if ok:
                    n_ok += 1; legs.append(f"TP{i}")
                    fill = getattr(r, "price", 0) or price
                    results.append(f"✅ TP{i} {tp} — ticket #{r.order}" + (f" @ {fill}" if otype == "MARKET" else ""))
                else:
                    results.append(f"❌ TP{i} {tp} — {r.comment if r else mt5.last_error()}")
            if n_ok:
                self.groups[sid] = {"tp1": sig.tps[0], "created": time.time(),
                                    "pending": otype != "MARKET", "be": False, "lock": False,
                                    "legs": legs, "tickets": []}
                self._save_groups()
            n = len(sig.tps)
            if n_ok == n:
                head = ("✅ TRADE PLACED SUCCESSFULLY" if otype == "MARKET"
                        else "✅ PENDING ORDER PLACED SUCCESSFULLY (waiting for price)")
            elif n_ok:
                head = f"⚠️ PARTIALLY PLACED — {n_ok}/{n} orders OK"
            else:
                head = "❌ TRADE FAILED — nothing placed"
            risk = abs(price - sig.sl) / info.trade_tick_size * info.trade_tick_value * lot * n_ok
            return (f"{head}\n\n"
                    f"{sig.side} {name}  |  {otype} @ {price}\n"
                    f"Lot: {lot} x {n_ok} = {round(lot * n_ok, 2)}\n"
                    f"SL: {sig.sl}  (risk ≈ ${risk:,.2f})\n"
                    f"Reason: {reason}\n\n" + "\n".join(results) + f"\n\nID: {sid}\n"
                    f"💰 Balance: {mt5.account_info().balance:,.2f}  |  Lot mode: {self.lot_mode()}")

    # ---------- management loop ----------
    def _set_sl(self, p, sl):
        r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": p.ticket,
                            "symbol": p.symbol, "sl": sl, "tp": p.tp, "magic": self.magic})
        return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE

    def manage(self):
        """Runs every few seconds. Returns a list of messages for Telegram.
        TP1 closed -> remaining legs SL to break-even. TP2 closed -> last leg SL to TP1.
        Pending orders older than PENDING_EXPIRY_MIN are cancelled."""
        # runs even when paused: open trades still get break-even / profit lock
        self.ensure()
        msgs = []
        pos = [p for p in (mt5.positions_get() or []) if p.magic == self.magic]
        ords = [o for o in (mt5.orders_get() or []) if o.magic == self.magic]
        by_sid = {}
        for p in pos:
            by_sid.setdefault(p.comment.split("|")[0], []).append(p)
        pend_sids = {o.comment.split("|")[0] for o in ords}

        for o in ords:  # expire stale pendings
            g = self.groups.get(o.comment.split("|")[0])
            if g and time.time() - g["created"] > self.expiry_min * 60:
                mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                msgs.append(f"⌛ Pending {o.comment} expired — cancelled.")

        for sid, g in list(self.groups.items()):
            plist = by_sid.get(sid, [])
            if not plist and sid not in pend_sids:
                del self.groups[sid]
                msgs.append(self._closed_report(sid, g))
                continue
            for p in plist:
                if p.ticket not in g.setdefault("tickets", []):
                    g["tickets"].append(p.ticket)
            if plist and g.get("pending") and sid not in pend_sids:
                g["pending"] = False
                p0 = plist[0]
                msgs.append(f"✅ TRADE OPENED SUCCESSFULLY — pending order filled\n"
                            f"{'BUY' if p0.type == mt5.POSITION_TYPE_BUY else 'SELL'} {p0.symbol} "
                            f"@ {p0.price_open} | {len(plist)} positions | SL {p0.sl}\nID: {sid}")
            if sid in pend_sids or not plist:
                continue  # still waiting for fills
            legs = {p.comment.split("|")[1] for p in plist if "|" in p.comment}
            placed = set(g.get("legs", ["TP1", "TP2", "TP3"]))
            buy = plist[0].type == mt5.POSITION_TYPE_BUY
            better = (lambda new, old: old == 0 or new > old) if buy else (lambda new, old: old == 0 or new < old)
            if self.be_after_tp1 and not g["be"] and "TP1" in placed and "TP1" not in legs:
                for p in plist:
                    if better(p.price_open, p.sl):
                        self._set_sl(p, p.price_open)
                g["be"] = True
                msgs.append(f"✅ {sid} TP1 hit → SL moved to break-even.")
            if self.lock_after_tp2 and not g["lock"] and {"TP1", "TP2"} <= placed and not legs & {"TP1", "TP2"}:
                for p in plist:
                    if better(g["tp1"], p.sl):
                        self._set_sl(p, g["tp1"])
                g["lock"] = True
                msgs.append(f"✅ {sid} TP2 hit → SL locked at TP1 {g['tp1']}.")
        self._save_groups()
        return msgs

    def _closed_report(self, sid, g):
        """Sum realised P/L of a finished signal from MT5 deal history."""
        try:
            pl, n = 0.0, 0
            for t in g.get("tickets", []):
                for d in mt5.history_deals_get(position=t) or []:
                    if d.entry in (1, 3):  # DEAL_ENTRY_OUT / OUT_BY
                        n += 1
                    pl += d.profit + d.commission + d.swap
            if n:
                icon = "🟢" if pl >= 0 else "🔴"
                return (f"🏁 {icon} Signal {sid} CLOSED — result {pl:+,.2f}\n"
                        f"💰 Balance: {mt5.account_info().balance:,.2f}")
        except Exception:
            log.exception("history for %s", sid)
        return f"🏁 Signal {sid} fully closed.\n💰 Balance: {mt5.account_info().balance:,.2f}"

    # ---------- manual close ----------
    def _close(self, p):
        info = mt5.symbol_info(p.symbol)
        t = mt5.symbol_info_tick(p.symbol)
        buy = p.type == mt5.POSITION_TYPE_BUY
        r = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol,
            "volume": p.volume, "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
            "price": t.bid if buy else t.ask, "deviation": 100, "magic": self.magic,
            "type_filling": self._filling(info), "comment": "CLOSE"})
        return r is not None and r.retcode == mt5.TRADE_RETCODE_DONE

    def close_ticket(self, ticket):
        with self.lock:
            self.ensure()
            p = next((p for p in mt5.positions_get() or [] if p.ticket == ticket), None)
            if not p:
                return f"ℹ️ Position #{ticket} is already closed."
            pl = p.profit
            if self._close(p):
                return (f"✅ POSITION CLOSED SUCCESSFULLY\n#{ticket} {p.symbol} {p.volume} lot  "
                        f"P/L {pl:+.2f}\n💰 Balance: {mt5.account_info().balance:,.2f}")
            return f"❌ Close failed for #{ticket}: {mt5.last_error()}"

    def close_group(self, sid):
        """Close every position and cancel every pending order of one signal."""
        with self.lock:
            self.ensure()
            n = pl = 0; failed = []
            for o in mt5.orders_get() or []:
                if o.magic == self.magic and o.comment.split("|")[0] == sid:
                    mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket}); n += 1
            for p in mt5.positions_get() or []:
                if p.magic == self.magic and p.comment.split("|")[0] == sid:
                    if self._close(p):
                        n += 1; pl += p.profit
                    else:
                        failed.append(p.ticket)
            if not n and not failed:
                return f"ℹ️ Trade {sid} has nothing open."
            self.groups.pop(sid, None); self._save_groups()
            warn = f"\n⚠️ Failed: {failed} — close in MT5!" if failed else ""
            return (f"✅ TRADE {sid} CLOSED SUCCESSFULLY\n{n} orders closed  P/L {pl:+.2f}{warn}\n"
                    f"💰 Balance: {mt5.account_info().balance:,.2f}")

    def open_positions(self):
        self.ensure()
        return [p for p in mt5.positions_get() or [] if p.magic == self.magic]

    def balance(self):
        self.ensure()
        a = mt5.account_info()
        pos = self.open_positions()
        return (f"💰 ACCOUNT  {a.login} ({a.server})\n"
                f"Balance:   {a.balance:,.2f} {a.currency}\n"
                f"Equity:    {a.equity:,.2f}\n"
                f"Floating:  {a.equity - a.balance:+,.2f}\n"
                f"Free margin: {a.margin_free:,.2f}\n"
                f"Open bot positions: {len(pos)}\n"
                f"Lot mode: {self.lot_mode()}\n"
                f"Bot: {'⛔ STOPPED' if self.halted else '🟢 RUNNING'}")

    # ---------- kill switch ----------
    def emergency_stop(self):
        """Halt new trades, cancel all pending, close all bot positions (3 retries)."""
        self.halted = True
        with self.lock:
            self.ensure()
            cancelled = closed = 0
            pl = 0.0
            bal_before = mt5.account_info().balance
            for attempt in range(3):
                for o in mt5.orders_get() or []:
                    if o.magic == self.magic:
                        r = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                        cancelled += r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
                for p in mt5.positions_get() or []:
                    if p.magic != self.magic:
                        continue
                    info = mt5.symbol_info(p.symbol)
                    t = mt5.symbol_info_tick(p.symbol)
                    buy = p.type == mt5.POSITION_TYPE_BUY
                    r = mt5.order_send({
                        "action": mt5.TRADE_ACTION_DEAL, "position": p.ticket, "symbol": p.symbol,
                        "volume": p.volume, "type": mt5.ORDER_TYPE_SELL if buy else mt5.ORDER_TYPE_BUY,
                        "price": t.bid if buy else t.ask, "deviation": 100, "magic": self.magic,
                        "type_filling": self._filling(info), "comment": "EMERGENCY"})
                    if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
                        closed += 1; pl += p.profit
                left = [p for p in mt5.positions_get() or [] if p.magic == self.magic] + \
                       [o for o in mt5.orders_get() or [] if o.magic == self.magic]
                if not left:
                    break
                time.sleep(1)
            self.groups.clear(); self._save_groups()
            warn = f"\n⚠️ {len(left)} still open — CLOSE MANUALLY IN MT5!" if left else ""
            bal = mt5.account_info().balance
            return (f"🛑 EMERGENCY STOP — ALL CLOSED\n\n"
                    f"Closed {closed} positions, cancelled {cancelled} pending.{warn}\n"
                    f"P/L of closed trades: {pl:+,.2f}\n"
                    f"Balance change: {bal - bal_before:+,.2f} (incl. commission)\n"
                    f"💰 Balance now: {bal:,.2f}\n\n"
                    "⛔ Bot halted — tap ▶️ Resume to accept signals again.")

    def status(self):
        self.ensure()
        acc = mt5.account_info()
        pos = [p for p in (mt5.positions_get() or []) if p.magic == self.magic]
        ords = [o for o in (mt5.orders_get() or []) if o.magic == self.magic]
        lines = [f"{'⛔ HALTED' if self.halted else '🟢 RUNNING'} | Bal {acc.balance:.2f} Eq {acc.equity:.2f}"]
        for p in pos:
            lines.append(f"{'BUY' if p.type == 0 else 'SELL'} {p.symbol} {p.volume} @ {p.price_open} P/L {p.profit:.2f} [{p.comment}]")
        for o in ords:
            lines.append(f"PENDING {o.symbol} {o.volume_current} @ {o.price_open} [{o.comment}]")
        return "\n".join(lines)
