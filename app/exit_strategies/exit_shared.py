from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ExitAction:
    ticket: Any
    symbol: str
    side: str
    volume: float
    reason: str


@dataclass
class PosState:
    anchor: float
    prev_price: float
    ticks_seen: int = 0
    ever_favorable: bool = False
    unfavorable_ticks: int = 0
    anchor_close: float = 0.0
    prev_close: float = 0.0
    closes_seen: int = 0
    prev_in_profit: Optional[bool] = None


def get_any(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj:

                return obj[k]
        return None
    for k in keys:
        if hasattr(obj, k):
            value = getattr(obj, k)
            return value
    return None


def pos_symbol(position):
    v = get_any(position, ("symbol",))
    return str(v) if v else None


def pos_side(position):
    t = get_any(position, ("type", "side", "direction"))
    if t is None:
        return None
    if isinstance(t, str):
        s = t.strip().lower()
        if s in ("buy", "long"):
            return "buy"
        if s in ("sell", "short"):
            return "sell"
        return None
    try:
        if int(t) == 0:
            return "buy"
        if int(t) == 1:
            return "sell"
    except Exception:
        return None
    return None


def pos_ticket(position):
    return get_any(position, ("ticket", "id", "position", "order"))


def pos_entry(position):
    v = get_any(position, ("price_open", "open_price", "entry_price", "price"))
    return float(v) if v not in (None, "") else None


def pos_volume(position):
    v = get_any(position, ("volume", "lots", "qty", "quantity"))
    return float(v) if v not in (None, "") else None


def pos_profit(position):
    v = get_any(position, ("profit", "pnl", "floating_profit"))
    return float(v) if v not in (None, "") else None


def is_break_even(position) -> bool:
    profit = getattr(position, "profit", None)
    if profit is None:
        profit = 0.0
    return profit >= 0.0
