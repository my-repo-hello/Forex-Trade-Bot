"""Minimal in-memory imitation of the MetaTrader5 API, for tests and dry runs.

Prices are static until you call set_price(); that also triggers SL/TP hits
and pending-order fills, so trade management can be tested end-to-end.
In Telegram (paper mode) send:  price XAUUSD 4500.5
"""
import itertools
import time
from types import SimpleNamespace as NS


class PaperMT5:
    # same numeric values as the real package
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT = 2, 3
    ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 4, 5
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_PENDING, TRADE_ACTION_SLTP = 1, 5, 6
    TRADE_ACTION_REMOVE = 8
    ORDER_TIME_GTC, ORDER_TIME_SPECIFIED = 0, 2
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE = 10008, 10009
    TRADE_RETCODE_INVALID = 10013

    SPECS = {  # symbol: (point, digits, tick_value per lot per tick, start price)
        "XAUUSD": (0.01, 2, 1.0, 4500.00),
        "XAGUSD": (0.001, 3, 5.0, 50.000),
        "EURUSD": (0.00001, 5, 1.0, 1.10000),
        "GBPUSD": (0.00001, 5, 1.0, 1.30000),
        "USDJPY": (0.001, 3, 0.67, 150.000),
        "BTCUSD": (0.01, 2, 0.01, 100000.0),
        "US30": (0.01, 2, 0.01, 45000.0),
        "NAS100": (0.01, 2, 0.01, 20000.0),
    }

    def __init__(self, balance=10000.0, spread_points=20):
        self.balance = balance
        self.spread_points = spread_points
        self.prices = {s: v[3] for s, v in self.SPECS.items()}
        self.positions, self.orders = {}, {}
        self._ids = itertools.count(1000)
        self.deals = []

    # ---- connection ----
    def initialize(self, **_): return True
    def shutdown(self): pass
    def last_error(self): return (0, "paper")
    def terminal_info(self): return NS(connected=True, trade_allowed=True)

    def account_info(self):
        return NS(login=0, server="PAPER", balance=self.balance, trade_mode=0,
                  equity=self.balance + sum(self._pl(p) for p in self.positions.values()),
                  margin_free=self.balance, currency="USD")

    # ---- symbols ----
    def symbol_select(self, name, _=True): return name in self.SPECS

    def symbol_info(self, name):
        if name not in self.SPECS:
            return None
        point, digits, tv, _ = self.SPECS[name]
        return NS(name=name, point=point, digits=digits, trade_tick_size=point,
                  trade_tick_value=tv, volume_min=0.01, volume_max=100.0,
                  volume_step=0.01, filling_mode=2, trade_stops_level=0, trade_mode=4)

    def symbol_info_tick(self, name):
        bid = self.prices[name]
        return NS(bid=bid, ask=round(bid + self.spread_points * self.SPECS[name][0], 8),
                  time=int(time.time()))

    def set_price(self, name, bid):
        """Move the market and simulate fills / SL / TP."""
        self.prices[name] = bid
        t = self.symbol_info_tick(name)
        for o in list(self.orders.values()):
            if o.symbol != name:
                continue
            buy = o.type in (2, 4)
            px = t.ask if buy else t.bid
            hit = {2: px <= o.price_open, 3: px >= o.price_open,
                   4: px >= o.price_open, 5: px <= o.price_open}[o.type]
            if hit:
                del self.orders[o.ticket]
                self._open(name, 0 if buy else 1, o.volume_current, o.price_open, o.sl, o.tp, o.magic, o.comment)
        for p in list(self.positions.values()):
            if p.symbol != name:
                continue
            px = t.bid if p.type == 0 else t.ask
            d = 1 if p.type == 0 else -1
            if (p.sl and (px - p.sl) * d <= 0) or (p.tp and (px - p.tp) * d >= 0):
                self._close(p, p.sl if p.sl and (px - p.sl) * d <= 0 else p.tp)

    def symbols_get(self, *_, **__):
        return tuple(NS(name=n) for n in self.SPECS)

    def history_deals_get(self, *_, position=None, **__):
        return tuple(d for d in self.deals if d.position_id == position)

    # ---- trading ----
    def positions_get(self, **_): return tuple(self.positions.values())
    def orders_get(self, **_): return tuple(self.orders.values())

    def _pl(self, p):
        point, _, tv, _ = self.SPECS[p.symbol]
        t = self.symbol_info_tick(p.symbol)
        px = t.bid if p.type == 0 else t.ask
        return (px - p.price_open) * (1 if p.type == 0 else -1) / point * tv * p.volume

    def _open(self, sym, typ, vol, price, sl, tp, magic, comment):
        tk = next(self._ids)
        self.positions[tk] = NS(ticket=tk, symbol=sym, type=typ, volume=vol, price_open=price,
                                sl=sl, tp=tp, magic=magic, comment=comment, profit=0.0)
        return tk

    def _close(self, p, price):
        point, _, tv, _ = self.SPECS[p.symbol]
        pl = (price - p.price_open) * (1 if p.type == 0 else -1) / point * tv * p.volume
        self.balance += pl
        self.deals.append(NS(position_id=p.ticket, entry=1, profit=pl, commission=0.0, swap=0.0))
        del self.positions[p.ticket]

    def order_send(self, r):
        ok = lambda tk, code=self.TRADE_RETCODE_DONE: NS(retcode=code, order=tk, comment="ok")
        a = r["action"]
        if a == self.TRADE_ACTION_DEAL:
            t = self.symbol_info_tick(r["symbol"])
            if "position" in r:
                p = self.positions.get(r["position"])
                if not p:
                    return NS(retcode=self.TRADE_RETCODE_INVALID, order=0, comment="no position")
                self._close(p, t.bid if p.type == 0 else t.ask)
                return ok(p.ticket)
            buy = r["type"] == self.ORDER_TYPE_BUY
            return ok(self._open(r["symbol"], r["type"], r["volume"], t.ask if buy else t.bid,
                                 r.get("sl", 0), r.get("tp", 0), r.get("magic", 0), r.get("comment", "")))
        if a == self.TRADE_ACTION_PENDING:
            tk = next(self._ids)
            self.orders[tk] = NS(ticket=tk, symbol=r["symbol"], type=r["type"], volume_current=r["volume"],
                                 price_open=r["price"], sl=r.get("sl", 0), tp=r.get("tp", 0),
                                 magic=r.get("magic", 0), comment=r.get("comment", ""),
                                 time_setup=int(time.time()))
            return ok(tk, self.TRADE_RETCODE_PLACED)
        if a == self.TRADE_ACTION_SLTP:
            p = self.positions[r["position"]]
            p.sl, p.tp = r["sl"], r["tp"]
            return ok(p.ticket)
        if a == self.TRADE_ACTION_REMOVE:
            self.orders.pop(r["order"], None)
            return ok(r["order"])
        return NS(retcode=self.TRADE_RETCODE_INVALID, order=0, comment="unsupported")
