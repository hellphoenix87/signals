from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import time
import uuid

import MetaTrader5 as mt5

from app.config.settings import Config


@dataclass(frozen=True)
class OCOConfig:
    """
    OCO straddle settings.

    Notes:
    - Many brokers reject MT5 pending-order expirations ("Invalid expiration").
      This implementation uses GTC orders and cancels them in code after expiry_seconds.
    """

    offset_pips: float = float(getattr(Config, "OCO_OFFSET_PIPS", 2.0) or 2.0)
    expiry_seconds: int = int(getattr(Config, "OCO_EXPIRY_SECONDS", 120) or 120)

    magic: int = int(getattr(Config, "MAGIC", 123456) or 123456)
    deviation: int = int(getattr(Config, "DEVIATION", 5) or 5)

    # Preferred filling mode; we still retry others for pending orders if needed
    filling: Optional[int] = getattr(Config, "FILLING_MODE", None)

    debug: bool = bool(getattr(Config, "OCO_DEBUG", True))


@dataclass
class OCOGroup:
    group_id: str
    symbol: str
    volume: float

    buy_stop_ticket: Optional[int] = None
    sell_stop_ticket: Optional[int] = None

    created_ts: float = 0.0
    expires_ts: float = 0.0

    comment: str = ""


class OCOStraddleManager:
    """
    Places and manages OCO straddles (BUY STOP + SELL STOP).

    Behavior:
    - Places two pending orders: BUY STOP above ask, SELL STOP below bid.
    - If one side disappears (filled/cancelled), the other side is cancelled (true OCO).
    - If expiry_seconds elapsed and neither side filled, both are cancelled.
    """

    def __init__(self, broker: Any = None, config: Optional[OCOConfig] = None):
        self._broker = broker  # optional; used for pip size if available
        self._cfg = config or OCOConfig()
        self._groups: dict[str, OCOGroup] = {}

    # -------------------------
    # Public API
    # -------------------------

    def place_straddle(
        self,
        *,
        symbol: str,
        volume: float,
        offset_pips: Optional[float] = None,
        expiry_seconds: Optional[int] = None,
        comment_prefix: str = "OCO",
    ) -> Optional[OCOGroup]:
        if not symbol or volume is None or float(volume) <= 0:
            return None

        if not self._select_symbol(symbol):
            return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            self._dbg(
                f"[OCO] symbol_info_tick returned None for {symbol}; last_error={mt5.last_error()}"
            )
            return None

        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            self._dbg(f"[OCO] bad tick for {symbol}: bid={bid} ask={ask}")
            return None

        off_pips_val = float(
            offset_pips if offset_pips is not None else self._cfg.offset_pips
        )
        off_price = self._pips_to_price(symbol, off_pips_val)
        if off_price <= 0:
            self._dbg(
                f"[OCO] invalid off_price for {symbol}: offset_pips={off_pips_val} off_price={off_price}"
            )
            return None

        # Enforce broker minimum stop distance (pending stop distance restriction)
        min_stop = self._min_stop_distance_price(symbol)
        if min_stop > 0 and off_price < min_stop:
            self._dbg(
                f"[OCO] offset too small for {symbol}: off_price={off_price} < min_stop={min_stop}; bumping to min_stop"
            )
            off_price = min_stop

        buy_price = self._normalize_price(symbol, ask + off_price)
        sell_price = self._normalize_price(symbol, bid - off_price)

        group_id = uuid.uuid4().hex[:12]
        now = time.time()
        exp_secs = int(
            expiry_seconds if expiry_seconds is not None else self._cfg.expiry_seconds
        )

        # Keep comment short (31 chars is a common broker limit)
        comment = f"{comment_prefix}:{group_id}"
        if len(comment) > 31:
            comment = comment[:31]

        grp = OCOGroup(
            group_id=group_id,
            symbol=symbol,
            volume=float(volume),
            created_ts=now,
            expires_ts=(now + exp_secs) if exp_secs > 0 else 0.0,
            comment=comment,
        )

        buy_ticket = self._send_pending(
            symbol=symbol,
            order_type=mt5.ORDER_TYPE_BUY_STOP,
            volume=float(volume),
            price=buy_price,
            comment=comment,
        )
        if buy_ticket is None:
            return None

        sell_ticket = self._send_pending(
            symbol=symbol,
            order_type=mt5.ORDER_TYPE_SELL_STOP,
            volume=float(volume),
            price=sell_price,
            comment=comment,
        )
        if sell_ticket is None:
            # best-effort rollback
            self._cancel_order(buy_ticket)
            return None

        grp.buy_stop_ticket = int(buy_ticket)
        grp.sell_stop_ticket = int(sell_ticket)

        self._groups[group_id] = grp
        return grp

    def on_tick(self) -> None:
        """
        Must be called frequently (e.g. every tick).
        Handles:
        - time expiry (cancel both)
        - OCO cancellation (if one leg disappears, cancel the other)
        """
        if not self._groups:
            return

        now = time.time()
        for group_id in list(self._groups.keys()):
            grp = self._groups[group_id]

            # time-based expiry (we do not rely on broker expiration)
            if grp.expires_ts and now >= grp.expires_ts:
                self._dbg(
                    f"[OCO] Expired group={group_id} symbol={grp.symbol}; cancelling both legs"
                )
                self._cancel_if_exists(grp.buy_stop_ticket)
                self._cancel_if_exists(grp.sell_stop_ticket)
                del self._groups[group_id]
                continue

            buy_alive = self._pending_order_exists(grp.buy_stop_ticket)
            sell_alive = self._pending_order_exists(grp.sell_stop_ticket)

            # True OCO: if one leg is gone, cancel the other leg.
            if (not buy_alive) and sell_alive:
                self._dbg(
                    f"[OCO] buy leg gone -> cancel sell leg. group={group_id} sell_ticket={grp.sell_stop_ticket}"
                )
                self._cancel_if_exists(grp.sell_stop_ticket)
                del self._groups[group_id]
                continue

            if (not sell_alive) and buy_alive:
                self._dbg(
                    f"[OCO] sell leg gone -> cancel buy leg. group={group_id} buy_ticket={grp.buy_stop_ticket}"
                )
                self._cancel_if_exists(grp.buy_stop_ticket)
                del self._groups[group_id]
                continue

            # Both gone -> nothing to manage
            if (not buy_alive) and (not sell_alive):
                del self._groups[group_id]

    def cancel_group(self, group_id: str) -> bool:
        grp = self._groups.get(group_id)
        if not grp:
            return False
        self._cancel_if_exists(grp.buy_stop_ticket)
        self._cancel_if_exists(grp.sell_stop_ticket)
        del self._groups[group_id]
        return True

    # -------------------------
    # MT5 helpers
    # -------------------------

    def _select_symbol(self, symbol: str) -> bool:
        try:
            ok = mt5.symbol_select(symbol, True)
            if not ok:
                self._dbg(
                    f"[OCO] symbol_select failed for {symbol}; last_error={mt5.last_error()}"
                )
            return bool(ok)
        except Exception as e:
            self._dbg(f"[OCO] symbol_select exception for {symbol}: {e}")
            return False

    def _send_pending(
        self,
        *,
        symbol: str,
        order_type: int,
        volume: float,
        price: float,
        comment: str,
    ) -> Optional[int]:
        """
        Sends a pending STOP order.

        Important:
        - Uses ORDER_TIME_GTC and does NOT set expiration (broker often rejects it).
        - Retries different filling modes because some brokers reject a given type_filling for pending orders.
        """
        base_req = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": float(volume),
            "type": int(order_type),
            "price": float(price),
            "deviation": int(self._cfg.deviation),
            "magic": int(self._cfg.magic),
            "comment": str(comment),
            "type_time": int(mt5.ORDER_TIME_GTC),
        }

        candidates: list[int] = []
        if self._cfg.filling is not None:
            candidates.append(int(self._cfg.filling))
        candidates.extend(
            [
                int(mt5.ORDER_FILLING_RETURN),
                int(mt5.ORDER_FILLING_FOK),
                int(mt5.ORDER_FILLING_IOC),
            ]
        )

        tried: set[int] = set()
        for filling in candidates:
            if filling in tried:
                continue
            tried.add(filling)

            req = dict(base_req)
            req["type_filling"] = int(filling)

            res = mt5.order_send(req)
            if res is None:
                self._dbg(
                    f"[OCO] order_send returned None for {symbol}; req={req}; last_error={mt5.last_error()}"
                )
                continue

            ret = getattr(res, "retcode", None)
            if ret in (10008, 10009):
                order_ticket = getattr(res, "order", None)
                try:
                    return int(order_ticket) if order_ticket else None
                except Exception:
                    return None

            # Log and decide whether to retry
            self._dbg(
                f"[OCO] order_send failed for {symbol}; retcode={ret}; comment={getattr(res,'comment','')}; res={res}; req={req}"
            )

            # Only keep retrying if MT5 says filling mode is unsupported; otherwise stop early
            if getattr(res, "comment", "") != "Unsupported filling mode":
                return None

        return None

    def _cancel_order(self, ticket: int) -> bool:
        if ticket is None:
            return False
        req = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(ticket)}
        res = mt5.order_send(req)
        if res is None:
            self._dbg(
                f"[OCO] cancel order_send returned None ticket={ticket}; last_error={mt5.last_error()}"
            )
            return False
        ret = getattr(res, "retcode", None)
        ok = ret in (10008, 10009)
        if not ok:
            self._dbg(f"[OCO] cancel failed ticket={ticket}; retcode={ret}; res={res}")
        return ok

    def _cancel_if_exists(self, ticket: Optional[int]) -> None:
        if ticket is None:
            return
        if self._pending_order_exists(ticket):
            self._cancel_order(ticket)

    def _pending_order_exists(self, ticket: Optional[int]) -> bool:
        if ticket is None:
            return False
        try:
            orders = mt5.orders_get(ticket=int(ticket))
            return bool(orders)
        except Exception:
            return False

    # -------------------------
    # Price helpers
    # -------------------------

    def _min_stop_distance_price(self, symbol: str) -> float:
        """
        Returns minimum stop distance in PRICE units (not pips).
        Many brokers reject pending stops closer than:
          max(trade_stops_level, trade_freeze_level) * point
        """
        info = mt5.symbol_info(symbol) if symbol else None
        if not info:
            return 0.0
        try:
            point = float(getattr(info, "point", 0.0) or 0.0)
            stops_level = int(getattr(info, "trade_stops_level", 0) or 0)
            freeze_level = int(getattr(info, "trade_freeze_level", 0) or 0)
            min_level = max(stops_level, freeze_level)
            return float(min_level) * point if point > 0 and min_level > 0 else 0.0
        except Exception:
            return 0.0

    def _pips_to_price(self, symbol: str, pips: float) -> float:
        get_pip_size = getattr(self._broker, "get_pip_size", None)
        if callable(get_pip_size) and symbol:
            try:
                pip_size = get_pip_size(symbol)
                if pip_size:
                    return float(pip_size) * float(pips)
            except Exception:
                pass

        info = mt5.symbol_info(symbol) if symbol else None
        if info:
            point = float(info.point)
            digits = int(info.digits)
            pip_size = point * 10.0 if digits in (3, 5) else point
            return pip_size * float(pips)

        # conservative fallback
        return (0.01 if "JPY" in symbol else 0.0001) * float(pips)

    def _normalize_price(self, symbol: str, price: float) -> float:
        info = mt5.symbol_info(symbol) if symbol else None
        if not info:
            return float(price)
        try:
            digits = int(getattr(info, "digits", 5) or 5)
            return round(float(price), digits)
        except Exception:
            return float(price)

    # -------------------------
    # Logging
    # -------------------------

    def _dbg(self, msg: str) -> None:
        if self._cfg.debug:
            print(msg)
