import os
os.environ["MT5_MODE"] = "paper"
os.environ["STATE_FILE"] = os.devnull + "x" if os.name == "nt" else "/tmp/fx_state_test.json"
os.environ["ALLOWED_CHAT_IDS"] = "1"

import pytest
from bot.executor import Executor, mt5
from bot.parser import ParseError, is_signal, parse_signal

SIG1 = """copy
GOLD SELL  4503-4504

SL 4520.00

TP1 4492.00

TP2 4479.00

TP3 4465.00"""

SIG2 = """copy
GOLD SELL LIMIT 4142-4143

SL 4151.00

TP1 4135.00

TP2 4127.00

TP3 4120.00
Free Signal TIPS"""


def test_parse_screenshot_1():
    s = parse_signal(SIG1)
    assert (s.symbol, s.side, s.entry_low, s.entry_high, s.sl) == ("XAUUSD", "SELL", 4503, 4504, 4520)
    assert s.tps == [4492, 4479, 4465] and s.order_hint == "AUTO"


def test_parse_screenshot_2_limit():
    s = parse_signal(SIG2)
    assert s.order_hint == "LIMIT" and (s.entry_low, s.entry_high) == (4142, 4143)


def test_parse_variants():
    s = parse_signal("XAUUSD/GOLD BUY NOW @ 4500\nSL: 4490\nTP 4510\nTP 4520")
    assert s.symbol == "XAUUSD" and s.side == "BUY" and s.tps == [4510, 4520]
    assert parse_signal("EUR/USD buy 1.1000 sl 1.0950 tp1 1.1050").symbol == "EURUSD"


def test_reject_no_sl_and_wrong_side():
    with pytest.raises(ParseError):
        parse_signal("GOLD SELL 4503 TP1 4492")
    with pytest.raises(ParseError):
        parse_signal("GOLD SELL 4503 SL 4490 TP1 4492")  # SL below entry on a sell


def test_stop_words_never_match_signals():
    from bot.main import is_stop_command
    assert is_stop_command("stop") and is_stop_command("Stop the trade immediately")
    assert is_stop_command("close all")
    assert not is_stop_command(SIG1) and not is_signal("stop")


@pytest.fixture
def ex():
    mt5.__init__()  # reset paper account
    e = Executor(); e.connect(); e.groups = {}; e.halted = False; e.recent = {}
    e.day = {"date": "", "balance": 0}
    return e


def decide_at(ex, sig, bid):
    mt5.set_price("XAUUSD", bid)
    return ex.decide(sig, mt5.symbol_info("XAUUSD"), mt5.symbol_info_tick("XAUUSD"))[0]


def test_decide_price_same_better_worse(ex):
    s = parse_signal(SIG1)
    assert decide_at(ex, s, 4503.5) == "MARKET"   # inside zone
    assert decide_at(ex, s, 4510) == "MARKET"     # better (higher) for sell
    assert decide_at(ex, s, 4501) == "MARKET"     # slightly worse, within tolerance
    assert decide_at(ex, s, 4495) == "LIMIT"      # ran away -> limit at zone
    assert decide_at(ex, s, 4490) is None         # past TP1
    assert decide_at(ex, s, 4521) is None         # past SL


def test_full_cycle_tp1_be_tp2_lock_and_kill(ex):
    mt5.set_price("XAUUSD", 4503.5)
    out = ex.place(parse_signal(SIG1), "S1")
    assert "PLACED SUCCESSFULLY" in out and out.count("✅") == 4 and len(mt5.positions_get()) == 3
    mt5.set_price("XAUUSD", 4491.5)                # TP1 hit
    assert any("break-even" in m for m in ex.manage())
    assert all(p.sl == p.price_open for p in mt5.positions_get())
    mt5.set_price("XAUUSD", 4478.5)                # TP2 hit
    assert any("locked" in m for m in ex.manage())
    assert mt5.positions_get()[0].sl == 4492
    assert "EMERGENCY" in ex.emergency_stop()
    assert not mt5.positions_get() and ex.halted
    assert "STOPPED" in ex.place(parse_signal(SIG1), "S2")


def test_limit_signal_places_pendings_and_kill_cancels(ex):
    mt5.set_price("XAUUSD", 4138)
    out = ex.place(parse_signal(SIG2), "S3")
    assert "LIMIT" in out and len(mt5.orders_get()) == 3
    ex.emergency_stop()
    assert not mt5.orders_get()


def test_signal_ids_unique_and_no_sl_reply():
    import asyncio
    from types import SimpleNamespace as NS
    import bot.main as m
    replies = []
    class Msg:
        def __init__(s, t): s.text, s.caption = t, None
        async def reply_text(s, t, **k): replies.append(t)
    async def send(t): await m.on_text(NS(message=Msg(t), effective_user=NS(id=1)), None)
    m.ex.halted = False
    asyncio.run(send("GOLD SELL 4503 TP1 4492"))
    assert "no SL" in replies[-1]
    mt5.set_price("XAUUSD", 4503.5)
    m.ex.recent = {}
    asyncio.run(send(SIG1)); asyncio.run(send(SIG1))
    assert "Duplicate" in replies[-1]           # same signal twice -> blocked
    asyncio.run(send(SIG1.replace("TP3 4465", "TP3 4466")))
    sids = {p.comment.split("|")[0] for p in mt5.positions_get()}
    assert len(sids) == 2                       # different signal, unique id


def test_lot_setting_balance_and_close_buttons(ex, tmp_path):
    import bot.main as m
    ex.settings_file = str(tmp_path / "s.json")
    assert "FIXED 0.05" in ex.set_fixed_lot(0.05)
    mt5.set_price("XAUUSD", 4503.5)
    out = ex.place(parse_signal(SIG1), "S9")
    assert "TRADE PLACED SUCCESSFULLY" in out and "Balance" in out
    assert all(p.volume == 0.05 for p in mt5.positions_get())
    m.ex = ex
    text, kb = m.positions_view()
    assert text.count("#") == 3 and len(kb.inline_keyboard) == 4
    t = mt5.positions_get()[0].ticket
    assert "POSITION CLOSED SUCCESSFULLY" in ex.close_ticket(t)
    assert len(mt5.positions_get()) == 2
    assert "CLOSED SUCCESSFULLY" in ex.close_group("S9") and not mt5.positions_get()
    assert "Balance" in ex.balance() and "FIXED 0.05" in ex.balance()
    assert "risk 1.0%" in ex.set_risk(1.0) and ex.fixed_lot is None
    assert "❌" in ex.set_fixed_lot(50)


def test_real_account_safety(ex, tmp_path):
    ex.settings_file = str(tmp_path / "s.json")
    # halted survives restart
    ex.halted = True
    e2 = Executor(); e2.settings_file = ex.settings_file; e2._load_settings()
    assert e2.halted
    ex.halted = False
    # duplicate signal blocked
    s = parse_signal(SIG1)
    assert not ex.is_duplicate(s) and ex.is_duplicate(s)
    # daily loss limit blocks new trades
    ex.day = {"date": __import__("datetime").date.today().isoformat(), "balance": 20000.0}
    mt5.set_price("XAUUSD", 4503.5)
    assert "DAILY LOSS LIMIT" in ex.place(s, "S50")
    ex.day = {"date": "", "balance": 0}
    # TP1 leg missing from placement -> no break-even jump
    ex.place(s, "S51")
    ex.groups["S51"]["legs"] = ["TP2", "TP3"]
    for p in list(mt5.positions_get()):
        if p.comment.endswith("TP1"):
            mt5.positions.pop(p.ticket)
    msgs = ex.manage()
    assert not any("break-even" in m for m in msgs)
    # closed signal reports realised result
    mt5.set_price("XAUUSD", 4521)  # SL hit
    assert any("CLOSED — result -" in m for m in ex.manage())
