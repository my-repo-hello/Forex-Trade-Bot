"""Parse free-text Telegram signals into a structured Signal."""
import re
from dataclasses import dataclass, field

ALIASES = {
    "GOLD": "XAUUSD", "XAU": "XAUUSD", "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "XAG": "XAGUSD",
    "US30": "US30", "DOW": "US30", "NAS100": "NAS100", "NASDAQ": "NAS100",
    "BTC": "BTCUSD", "BTCUSD": "BTCUSD",
}
NUM = r"(\d+(?:\.\d+)?)"


@dataclass
class Signal:
    symbol: str
    side: str                    # "BUY" | "SELL"
    entry_low: float | None      # entry zone (low == high for single price)
    entry_high: float | None
    sl: float
    tps: list[float] = field(default_factory=list)
    order_hint: str = "AUTO"     # AUTO | LIMIT | STOP | NOW

    @property
    def entry(self):
        if self.entry_low is None:
            return None
        return (self.entry_low + self.entry_high) / 2


class ParseError(ValueError):
    pass


def _symbol(text: str) -> str:
    for word in re.findall(r"[A-Z0-9]{3,8}", text):
        if word in ALIASES:
            return ALIASES[word]
    m = re.search(r"\b([A-Z]{6})\b", text)  # generic pair like EURUSD
    if m:
        return m.group(1)
    raise ParseError("symbol not found")


def parse_signal(raw: str) -> Signal:
    text = raw.upper().replace(",", "")
    text = re.sub(r"\b([A-Z]{3})/([A-Z]{3})\b", r"\1\2", text)  # EUR/USD -> EURUSD
    text = text.replace("/", " ")                                  # XAUUSD/GOLD -> XAUUSD GOLD
    symbol = _symbol(text)

    side_m = re.search(r"\b(BUY|SELL|LONG|SHORT)\b", text)
    if not side_m:
        raise ParseError("BUY/SELL not found")
    side = {"LONG": "BUY", "SHORT": "SELL"}.get(side_m.group(1), side_m.group(1))

    hint = "AUTO"
    if re.search(r"\b(BUY|SELL)\s*LIMIT\b", text):
        hint = "LIMIT"
    elif re.search(r"\b(BUY|SELL)\s*STOP\b", text):
        hint = "STOP"
    elif re.search(r"\b(NOW|MARKET|CMP)\b", text):
        hint = "NOW"

    # entry: "ENTRY 2350", "@ 2350", "BUY GOLD 2350-2345", "ZONE 2350 2345"
    entry_low = entry_high = None
    em = (re.search(r"(?:ENTRY|ENTER|@|PRICE|ZONE)\s*[:\-]?\s*" + NUM + r"(?:\s*[-~TO ]+\s*" + NUM + r")?", text)
          or re.search(r"\b(?:BUY|SELL)\b[^\n\d]*" + NUM + r"(?:\s*[-~]\s*" + NUM + r")?", text))
    if em:
        a = float(em.group(1)); b = float(em.group(2)) if em.group(2) else a
        entry_low, entry_high = min(a, b), max(a, b)

    sl_m = re.search(r"\b(?:SL|STOP\s*LOSS|STOPLOSS|S\.L)\s*[:\-=@]?\s*" + NUM, text)
    if not sl_m:
        raise ParseError("SL not found — refusing to trade without stop loss")
    sl = float(sl_m.group(1))

    tps = [float(v) for _, v in re.findall(r"\bT\.?P(\d?)\s*[:\-=@]?\s*" + NUM, text)]
    if not tps:
        raise ParseError("no TP found")

    # sanity: SL / TP on the correct side of entry
    ref = (entry_low + entry_high) / 2 if entry_low else None
    if ref:
        if side == "BUY" and not (sl < ref and all(tp > ref for tp in tps)):
            raise ParseError("BUY levels inconsistent (SL must be below, TPs above entry)")
        if side == "SELL" and not (sl > ref and all(tp < ref for tp in tps)):
            raise ParseError("SELL levels inconsistent (SL must be above, TPs below entry)")

    return Signal(symbol, side, entry_low, entry_high, sl, tps, hint)


def is_signal(raw: str) -> bool:
    t = raw.upper()
    return bool(re.search(r"\b(BUY|SELL|LONG|SHORT)\b", t) and re.search(r"\bS\.?L\b|STOP\s*LOSS", t))
