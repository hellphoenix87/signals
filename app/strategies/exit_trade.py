# -- EXIT ON FIRST REVERSED TICK IF IN PROFIT + SOFT SL (MONEY / PRICE / PIPS) --

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import MetaTrader5 as mt5

from app.config.settings import Config


def create_exit_trade(
    broker: Any, risk_manager: Any, config: Optional["ExitTradeConfig"] = None
) -> "ExitTrade":
    """Provider for DI wiring."""
    return ExitTrade(broker=broker, risk_manager=risk_manager, config=config)


@dataclass(frozen=True)
class ExitTradeConfig:
    """
    Tick-driven exit configuration.

    Soft SL (checked first; can exit while losing):
      - max_loss_money: account-currency loss threshold (exit when profit <= -max_loss_money)
      - max_loss_price: raw PRICE distance from entry (e.g., EURUSD 0.00020 = 20 points)
      - max_loss_pips: pip-based distance from entry (used if max_loss_price == 0)

    Reversal exit:
      - exit on first tick-to-tick reversal IF in profit (min_profit_pips threshold)

    Notes:
      - Price used is the "closeable" price: BUY closes at bid, SELL closes at ask.
      - This strategy does NOT set broker SL/TP. It only returns ExitAction.
    """

    buffer_pips: float = Config.EXIT_BUFFER_PIPS
    buffer_start_tick: int = Config.EXIT_BUFFER_START_TICK
    eps_pips: float = Config.EXIT_EPS_PIPS

    exit_on_first_tick_not_favorable: bool = Config.EXIT_ON_FIRST_TICK_NOT_FAVORABLE

    # Disabled in this implementation
    exit_on_first_profit_tick: bool = False

    # Main rule (config-driven)
    exit_on_first_reversal_in_profit: bool = bool(
        getattr(Config, "EXIT_ON_FIRST_REVERSAL_IN_PROFIT", True)
    )
    treat_flat_as_reversal: bool = bool(
        getattr(Config, "EXIT_TREAT_FLAT_AS_REVERSAL", False)
    )

    max_unfavorable_ticks: int = 0

    # Soft SL controls
    max_loss_money: float = float(getattr(Config, "EXIT_MAX_LOSS_MONEY", 0.0) or 0.0)
    max_loss_price: float = float(getattr(Config, "EXIT_MAX_LOSS_PRICE", 0.0) or 0.0)
    max_loss_pips: float = float(getattr(Config, "EXIT_MAX_LOSS_PIPS", 0.0) or 0.0)

    # Profit threshold for "in profit" checks (0 => any profit)
    min_profit_pips: float = float(getattr(Config, "EXIT_MIN_PROFIT_PIPS", 0.0) or 0.0)

    # Early-abort (spread-safe alternative to "first tick not favorable")
    early_abort_enabled: bool = bool(getattr(Config, "EXIT_EARLY_ABORT_ENABLED", False))
    early_abort_ticks: int = int(getattr(Config, "EXIT_EARLY_ABORT_TICKS", 0) or 0)
    early_abort_loss_pips: float = float(
        getattr(Config, "EXIT_EARLY_ABORT_LOSS_PIPS", 0.0) or 0.0
    )

    # Grace period for money soft-SL to avoid instant exits from spread right after entry
    soft_sl_money_grace_ticks: int = int(
        getattr(Config, "EXIT_SOFT_SL_MONEY_GRACE_TICKS", 0) or 0
    )


@dataclass
class ExitAction:
    """Instruction to close an open position (execution layer will translate to buy/sell)."""

    ticket: Any
    symbol: str
    side: str  # "buy" or "sell" (close order side; opposite of position side)
    volume: float
    reason: str


@dataclass
class _PosState:
    anchor: float
    prev_price: float  # last observed closeable price for this position (bid for buy, ask for sell)
    ticks_seen: int = 0
    ever_favorable: bool = False
    unfavorable_ticks: int = 0


class ExitTrade:
    """
    Tick-driven exit logic with:
    - optional "exit on first tick not favorable"
    - "exit on first reversal tick if in profit"
    - internal trailing anchor + buffer funnel (backup)
    - soft SL by money/price/pips

    IMPORTANT:
    - This does NOT set/modify broker SL/TP.
    - It only decides when to exit and returns ExitAction(s).
    """

    def __init__(
        self,
        broker: Any,
        risk_manager: Any,
        config: Optional[ExitTradeConfig] = None,
    ):
        self._broker = broker
        self._risk_manager = risk_manager
        self._config = config or ExitTradeConfig()
        self._state_by_ticket: dict[Any, _PosState] = {}

    def on_tick(self, tick: Any) -> list[ExitAction]:
        """
        Process a tick (or None) and return 0..N exit actions for currently open positions.

        IMPORTANT: We do NOT bail out when tick is None.
        We will still evaluate each open position by polling mt5.symbol_info_tick(symbol).
        """
        positions = self._safe_get_positions()
        if not positions:
            self._state_by_ticket.clear()
            return []

        open_tickets: set[Any] = set()
        actions: list[ExitAction] = []

        for pos in positions:
            ticket = self._pos_ticket(pos)
            if ticket is None:
                continue
            open_tickets.add(ticket)

            action = self._evaluate_position(pos, tick)
            if action is not None:
                actions.append(action)

        self._prune_states(open_tickets)
        return actions

    # -------------------------
    # Core rule implementation
    # -------------------------

    def _evaluate_position(self, position: Any, tick: Any) -> Optional[ExitAction]:
        symbol = self._pos_symbol(position)
        side = self._pos_side(position)  # position side: "buy"/"sell"
        ticket = self._pos_ticket(position)
        entry = self._pos_entry(position)
        volume = self._pos_volume(position)

        if not symbol or not side or ticket is None or entry is None or volume is None:
            return None

        # Always ensure we have the correct tick for THIS symbol.
        tick_symbol = getattr(tick, "symbol", None) if tick is not None else None
        if tick is None or (tick_symbol and str(tick_symbol) != str(symbol)):
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return None

        # price is the closeable price: buy closes at bid, sell closes at ask
        price = self._tick_price_for_position_side(tick=tick, position_side=side)
        if price is None:
            return None

        # Create/update per-ticket state first so we can apply grace logic reliably
        st = self._state_by_ticket.get(ticket)

        if st is None:
            favorable_now = self._is_favorable_vs_entry(
                symbol=symbol, position_side=side, entry=entry, price=price
            )

            if (not favorable_now) and bool(
                getattr(self._config, "exit_on_first_tick_not_favorable", False)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="first_tick_not_favorable",
                )

            st = _PosState(
                anchor=float(price),
                prev_price=float(price),
                ticks_seen=1,
                ever_favorable=bool(favorable_now),
                unfavorable_ticks=0 if favorable_now else 1,
            )
            self._state_by_ticket[ticket] = st
        else:
            st.ticks_seen += 1

        # ---- Soft stop loss (MONEY in account currency) with grace ticks ----
        # Exit when MT5 position.profit <= -max_loss_money,
        # but only after N ticks to avoid instant spread-trigger exits.
        max_loss_money = float(getattr(self._config, "max_loss_money", 0.0) or 0.0)
        grace = int(getattr(self._config, "soft_sl_money_grace_ticks", 0) or 0)
        if max_loss_money > 0 and (grace <= 0 or st.ticks_seen >= grace):
            profit = self._pos_profit(position)
            if profit is None:
                profit = self._mt5_profit_by_ticket(ticket)

            if profit is not None and float(profit) <= -max_loss_money:
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason=f"max_loss_money({max_loss_money})",
                )

        # "In profit" threshold
        min_profit_pips = float(getattr(self._config, "min_profit_pips", 0.0) or 0.0)
        min_profit_price = (
            self._pips_to_price(symbol=symbol, pips=min_profit_pips) or 0.0
        )

        def _net_profit_ok() -> bool:
            if side == "buy":
                return price > (entry + min_profit_price)
            return price < (entry - min_profit_price)

        # ---- Soft stop loss (RAW PRICE distance) ----
        max_loss_price = float(getattr(self._config, "max_loss_price", 0.0) or 0.0)
        if max_loss_price > 0:
            if side == "buy":
                if price <= (entry - max_loss_price):
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason=f"max_loss_price({max_loss_price})",
                    )
            else:
                if price >= (entry + max_loss_price):
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason=f"max_loss_price({max_loss_price})",
                    )

        # ---- Soft stop loss (PIPS distance; used if raw price stop is disabled) ----
        max_loss_pips = float(getattr(self._config, "max_loss_pips", 0.0) or 0.0)
        if max_loss_price <= 0 and max_loss_pips > 0:
            loss_price_from_pips = (
                self._pips_to_price(symbol=symbol, pips=max_loss_pips) or 0.0
            )
            if side == "buy":
                if price <= (entry - loss_price_from_pips):
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason=f"max_loss_pips({max_loss_pips})",
                    )
            else:
                if price >= (entry + loss_price_from_pips):
                    return self._exit_action(
                        ticket=ticket,
                        symbol=symbol,
                        position_side=side,
                        volume=volume,
                        reason=f"max_loss_pips({max_loss_pips})",
                    )

        # ---- Early-abort (after N ticks, if still not favorable AND losing >= X pips) ----
        if (
            bool(getattr(self._config, "early_abort_enabled", False))
            and int(getattr(self._config, "early_abort_ticks", 0) or 0) > 0
            and float(getattr(self._config, "early_abort_loss_pips", 0.0) or 0.0) > 0
        ):
            n_ticks = int(getattr(self._config, "early_abort_ticks", 0) or 0)
            loss_pips_th = float(
                getattr(self._config, "early_abort_loss_pips", 0.0) or 0.0
            )

            # Only consider early-abort if we've never been favorable yet (i.e., "immediately wrong" trades)
            if (not st.ever_favorable) and (st.ticks_seen >= n_ticks):
                pip_price = self._pips_to_price(symbol=symbol, pips=1.0) or 0.0
                if pip_price > 0:
                    adverse = 0.0
                    if side == "buy":
                        adverse = max(0.0, float(entry) - float(price))
                    else:
                        adverse = max(0.0, float(price) - float(entry))

                    adverse_pips = adverse / pip_price
                    if adverse_pips >= loss_pips_th:
                        return self._exit_action(
                            ticket=ticket,
                            symbol=symbol,
                            position_side=side,
                            volume=volume,
                            reason=f"early_abort({n_ticks}t,{loss_pips_th}p)",
                        )

        # ---- Reversal-in-profit exit ----
        if bool(getattr(self._config, "exit_on_first_reversal_in_profit", False)):
            prev = float(st.prev_price)
            if bool(getattr(self._config, "treat_flat_as_reversal", False)):
                reversal = (price <= prev) if side == "buy" else (price >= prev)
            else:
                reversal = (price < prev) if side == "buy" else (price > prev)

            if reversal and _net_profit_ok():
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="first_reversal_in_profit",
                )

        # Update prev price after reversal check
        st.prev_price = float(price)

        favorable_now = self._is_favorable_vs_entry(
            symbol=symbol, position_side=side, entry=entry, price=price
        )
        if favorable_now:
            st.ever_favorable = True
        else:
            if not st.ever_favorable:
                st.unfavorable_ticks += 1

        max_unfav = int(getattr(self._config, "max_unfavorable_ticks", 0) or 0)
        if (
            (max_unfav > 0)
            and (not st.ever_favorable)
            and (st.unfavorable_ticks >= max_unfav)
        ):
            return self._exit_action(
                ticket=ticket,
                symbol=symbol,
                position_side=side,
                volume=volume,
                reason=f"max_unfavorable_ticks({max_unfav})",
            )

        # ---- Anchor update / buffer breach trailing ----
        eps = self._pips_to_price(symbol=symbol, pips=self._config.eps_pips) or 0.0
        if self._is_favorable_vs_anchor(
            position_side=side, anchor=st.anchor, price=price, eps=eps
        ):
            st.anchor = float(price)
            return None

        if st.ticks_seen < max(1, int(self._config.buffer_start_tick)):
            return None

        buf = self._pips_to_price(symbol=symbol, pips=self._config.buffer_pips)
        if buf is None:
            return None

        if side == "buy":
            if price <= (st.anchor - buf) and _net_profit_ok():
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="buffer_breach",
                )
        else:
            if price >= (st.anchor + buf) and _net_profit_ok():
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="buffer_breach",
                )

        return None

    def _exit_action(
        self,
        *,
        ticket: Any,
        symbol: str,
        position_side: str,
        volume: float,
        reason: str,
    ) -> ExitAction:
        close_side = "sell" if position_side == "buy" else "buy"
        return ExitAction(
            ticket=ticket,
            symbol=symbol,
            side=close_side,
            volume=float(volume),
            reason=reason,
        )

    # -------------------------
    # Helpers: positions & ticks
    # -------------------------

    def _safe_get_positions(self):
        getter = getattr(self._broker, "get_open_positions", None)
        if callable(getter):
            return getter()
        return []

    def _prune_states(self, open_tickets: set[Any]) -> None:
        for ticket in list(self._state_by_ticket.keys()):
            if ticket not in open_tickets:
                del self._state_by_ticket[ticket]

    def _pos_ticket(self, position: Any) -> Any:
        return self._get_any(position, ("ticket", "id", "position", "order"))

    def _pos_symbol(self, position: Any) -> Optional[str]:
        v = self._get_any(position, ("symbol",))
        return str(v) if v else None

    def _pos_entry(self, position: Any) -> Optional[float]:
        v = self._get_any(
            position, ("price_open", "open_price", "entry_price", "price")
        )
        return float(v) if v not in (None, "") else None

    def _pos_volume(self, position: Any) -> Optional[float]:
        v = self._get_any(position, ("volume", "lots", "qty", "quantity"))
        return float(v) if v not in (None, "") else None

    def _pos_profit(self, position: Any) -> Optional[float]:
        """
        Try to read floating profit from the position object.
        MT5 positions typically expose 'profit'.
        """
        v = self._get_any(position, ("profit", "pnl", "floating_profit"))
        return float(v) if v not in (None, "") else None

    def _mt5_profit_by_ticket(self, ticket: Any) -> Optional[float]:
        """
        Fallback if broker position objects don't include profit.
        """
        if ticket is None:
            return None
        try:
            # Most MT5 builds support ticket=...
            try:
                positions = mt5.positions_get(ticket=ticket)
            except TypeError:
                # Fallback for older wrappers: filter manually
                positions = mt5.positions_get()
                if positions:
                    positions = [
                        p for p in positions if getattr(p, "ticket", None) == ticket
                    ]

            if not positions:
                return None

            p0 = positions[0]
            v = getattr(p0, "profit", None)
            return float(v) if v is not None else None
        except Exception:
            return None

    def _pos_side(self, position: Any) -> Optional[str]:
        t = self._get_any(position, ("type", "side", "direction"))
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

    def _get_any(self, obj: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(obj, dict):
            for k in keys:
                if k in obj:
                    return obj[k]
            return None
        for k in keys:
            if hasattr(obj, k):
                return getattr(obj, k)
        return None

    def _tick_price_for_position_side(
        self, *, tick: Any, position_side: str
    ) -> Optional[float]:
        px = (
            getattr(tick, "bid", None)
            if position_side == "buy"
            else getattr(tick, "ask", None)
        )
        return float(px) if px not in (None, "") else None

    def _is_favorable_vs_entry(
        self, *, symbol: str, position_side: str, entry: float, price: float
    ) -> bool:
        eps = self._pips_to_price(symbol=symbol, pips=self._config.eps_pips) or 0.0
        if position_side == "buy":
            return price > (entry + eps)
        return price < (entry - eps)

    def _pips_to_price(self, *, symbol: str, pips: float) -> Optional[float]:
        get_pip_size = getattr(self._broker, "get_pip_size", None)
        if callable(get_pip_size) and symbol:
            pip_size = get_pip_size(symbol)
            if pip_size:
                return float(pip_size) * float(pips)

        info = mt5.symbol_info(symbol) if symbol else None
        if info:
            point = float(info.point)
            digits = int(info.digits)
            pip_size = point * 10.0 if digits in (3, 5) else point
            return pip_size * float(pips)

        return 0.0001 * float(pips)

    def _get_spread(self, tick: Any) -> float:
        if tick is None:
            return 0.0
        bid = getattr(tick, "bid", None)
        ask = getattr(tick, "ask", None)
        try:
            if bid is None or ask is None:
                return 0.0
            bid_f = float(bid)
            ask_f = float(ask)
            if bid_f <= 0 or ask_f <= 0:
                return 0.0
            return max(0.0, ask_f - bid_f)
        except Exception:
            return 0.0

    def _is_favorable_vs_anchor(
        self, *, position_side: str, anchor: float, price: float, eps: float
    ) -> bool:
        a = float(anchor)
        p = float(price)
        e = float(eps or 0.0)
        if position_side == "buy":
            return p > (a + e)
        return p < (a - e)
