# -- HYBRID EXIT: tick-driven protection + M1 candle-close profit-taking --

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import time
import threading
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
    Hybrid exit configuration.

    Tick-driven (protective):
      - optional "exit on first tick not favorable" (usually OFF)
      - soft SL by money/price/pips
      - early-abort (if never favorable after N ticks and adverse >= X pips)

    Profit-taking:
      - either tick-based OR candle-close based (recommended with MTF entries)
      - reversal-in-profit + anchor/buffer trailing
      - optionally HTF-gated (blocks profit exits while HTF supports the position)
    """

    # Trailing buffer (pips)
    buffer_pips: float = float(getattr(Config, "EXIT_BUFFER_PIPS", 0.5) or 0.5)
    buffer_start_tick: int = int(getattr(Config, "EXIT_BUFFER_START_TICK", 3) or 3)
    buffer_start_candle: int = int(getattr(Config, "EXIT_BUFFER_START_CANDLE", 2) or 2)

    # Epsilon (pips) to avoid flip-flopping on equal prices
    eps_pips: float = float(getattr(Config, "EXIT_EPS_PIPS", 0.0) or 0.0)

    # Tick-only noisy rule (normally OFF)
    exit_on_first_tick_not_favorable: bool = bool(
        getattr(Config, "EXIT_ON_FIRST_TICK_NOT_FAVORABLE", False)
    )

    # Disabled in this implementation (kept for compatibility)
    exit_on_first_profit_tick: bool = False

    # Profit rule (reversal in profit)
    exit_on_first_reversal_in_profit: bool = bool(
        getattr(Config, "EXIT_ON_FIRST_REVERSAL_IN_PROFIT", True)
    )
    treat_flat_as_reversal: bool = bool(
        getattr(Config, "EXIT_TREAT_FLAT_AS_REVERSAL", False)
    )

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

    # ----------------------------
    # Higher-timeframe gating
    # ----------------------------
    # If enabled, only profit-taking exits (reversal/buffer) are gated.
    # Protective exits (soft SL / early abort) are NOT gated.
    htf_filter_enabled: bool = bool(getattr(Config, "EXIT_HTF_FILTER_ENABLED", False))
    htf_stale_seconds: int = int(getattr(Config, "EXIT_HTF_STALE_SECONDS", 180) or 180)
    htf_use_m15: bool = bool(getattr(Config, "EXIT_HTF_USE_M15", True))
    htf_use_m5: bool = bool(getattr(Config, "EXIT_HTF_USE_M5", True))

    # ----------------------------
    # Hybrid mode switches
    # ----------------------------
    profit_exits_on_tick: bool = bool(
        getattr(Config, "EXIT_PROFIT_EXITS_ON_TICK", True)
    )
    profit_exits_on_candle_close: bool = bool(
        getattr(Config, "EXIT_PROFIT_EXITS_ON_CANDLE_CLOSE", False)
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
    # Tick-based state
    anchor: float
    prev_price: float  # last observed closeable tick price (bid for buy, ask for sell)
    ticks_seen: int = 0
    ever_favorable: bool = False
    unfavorable_ticks: int = 0

    # Candle-close profit state
    anchor_close: float = 0.0
    prev_close: float = 0.0
    closes_seen: int = 0


class ExitTrade:
    """
    Hybrid exit logic:

    - on_tick(): protective exits (soft SL / early abort). Profit exits only if profit_exits_on_tick=True.
    - on_candle_close(): profit exits (reversal/buffer) on closed M1 candle if profit_exits_on_candle_close=True.

    Improvements:
    - Debounce/cooldown for repeated exits per ticket.
    - Dynamic trailing buffer (ATR-based if available).
    - Partial close support (configurable).
    - Per-symbol min_profit_pips.
    - Logging for exit reasons and state.
    - Graceful handling of MT5/broker errors.
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

        # HTF context updated externally (orchestrator / strategy)
        self._bias_by_symbol: dict[str, dict[str, Any]] = {}

        # Improvements
        self._last_exit_time: dict[Any, float] = {}
        self._exit_cooldown: float = float(
            getattr(Config, "EXIT_COOLDOWN_SECONDS", 2.0) or 2.0
        )
        self._partial_close_ratio: float = float(
            getattr(Config, "EXIT_PARTIAL_CLOSE_RATIO", 1.0) or 1.0
        )
        self._lock = threading.Lock()
        self._min_profit_pips_by_symbol: dict[str, float] = getattr(
            Config, "EXIT_MIN_PROFIT_PIPS_BY_SYMBOL", {}
        )

    # -------------------------
    # External HTF context
    # -------------------------

    def update_bias(
        self,
        symbol: str,
        *,
        m5: Optional[str] = None,
        m15: Optional[str] = None,
        asof_epoch: Optional[float] = None,
    ) -> None:
        """Store latest M5/M15 bias/confirmation for symbol."""
        if not symbol:
            return
        sym = str(symbol)

        row = self._bias_by_symbol.get(sym) or {}
        if m5 is not None:
            row["m5"] = str(m5).lower()
        if m15 is not None:
            row["m15"] = str(m15).lower()
        row["ts"] = float(asof_epoch if asof_epoch is not None else time.time())
        self._bias_by_symbol[sym] = row

    def _htf_allows_profit_exit(self, *, symbol: str, position_side: str) -> bool:
        """
        Returns False to BLOCK profit-taking exits when HTF bias still supports the trade.
        Protective exits should NOT use this filter.
        """
        if not bool(getattr(self._config, "htf_filter_enabled", False)):
            return True

        info = self._bias_by_symbol.get(str(symbol))
        if not info:
            return True  # no context -> don't block

        # stale context -> don't block
        ts = float(info.get("ts", 0.0) or 0.0)
        stale_s = int(getattr(self._config, "htf_stale_seconds", 0) or 0)
        if stale_s > 0 and (time.time() - ts) > stale_s:
            return True

        m15 = (info.get("m15") or "hold").lower()
        m5 = (info.get("m5") or "hold").lower()

        supportive = "buy" if position_side == "buy" else "sell"
        opposing = "sell" if position_side == "buy" else "buy"

        use_m15 = bool(getattr(self._config, "htf_use_m15", True))
        use_m5 = bool(getattr(self._config, "htf_use_m5", True))

        # If M15 still supports the position, block profit exits.
        if use_m15 and m15 == supportive:
            return False

        # If M15 flipped against, allow profit exits.
        if use_m15 and m15 == opposing:
            return True

        # If M15 is neutral/hold (or disabled), optionally require M5 opposing to allow exit.
        if use_m5:
            return m5 == opposing

        return True

    # -------------------------
    # Public API
    # -------------------------

    def on_tick(self, tick: Any) -> list[ExitAction]:
        """
        Process a tick (or None) and return 0..N exit actions for open positions.

        Protective exits are always evaluated here.
        Profit exits are evaluated here only if EXIT_PROFIT_EXITS_ON_TICK=True.
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

            # Debounce exit actions
            if not self._should_exit(ticket):
                continue

            try:
                action = self._evaluate_position_on_tick(pos, tick)
                if action is not None:
                    self._log_exit_action(action, pos, tick)
                    actions.append(action)
            except Exception as exc:
                self._log_exit_error(ticket, exc)

        self._prune_states(open_tickets)
        return actions

    def on_candle_close(
        self, *, symbol: str, close_price: float, asof_epoch: Optional[float] = None
    ) -> list[ExitAction]:
        """
        Evaluate PROFIT exits on closed M1 candle for a symbol.

        Call this once per new CLOSED M1 candle (i.e., when candle time changes).
        """
        _ = asof_epoch  # reserved for future logging/analytics
        if not bool(getattr(self._config, "profit_exits_on_candle_close", False)):
            return []

        if not symbol:
            return []

        try:
            close_px = float(close_price)
        except Exception:
            return []

        positions = self._safe_get_positions()
        if not positions:
            self._state_by_ticket.clear()
            return []

        actions: list[ExitAction] = []
        for pos in positions:
            if str(self._pos_symbol(pos) or "") != str(symbol):
                continue
            ticket = self._pos_ticket(pos)
            if ticket is None or not self._should_exit(ticket):
                continue
            try:
                action = self._evaluate_position_on_candle_close(
                    pos, close_price=close_px
                )
                if action is not None:
                    self._log_exit_action(action, pos, None)
                    actions.append(action)
            except Exception as exc:
                self._log_exit_error(ticket, exc)
        return actions

    # --- Debounce/cooldown for repeated exits per ticket ---
    def _should_exit(self, ticket: Any, cooldown: Optional[float] = None) -> bool:
        cooldown = cooldown if cooldown is not None else self._exit_cooldown
        now = time.time()
        last = self._last_exit_time.get(ticket, 0)
        if now - last < cooldown:
            return False
        self._last_exit_time[ticket] = now
        return True

    # --- Dynamic trailing buffer (ATR-based if available) ---
    def _dynamic_buffer(self, symbol: str, fallback_pips: float) -> float:
        get_atr = getattr(self._broker, "get_atr", None)
        if callable(get_atr):
            try:
                atr = float(get_atr(symbol, period=14))
                if atr > 0:
                    return atr
            except Exception:
                pass
        return fallback_pips

    # --- Per-symbol min_profit_pips ---
    def _get_min_profit_pips(self, symbol: str) -> float:
        if symbol in self._min_profit_pips_by_symbol:
            return float(self._min_profit_pips_by_symbol[symbol])
        return float(getattr(self._config, "min_profit_pips", 0.0) or 0.0)

    # --- Partial close support ---
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
        close_volume = float(volume) * self._partial_close_ratio
        return ExitAction(
            ticket=ticket,
            symbol=symbol,
            side=close_side,
            volume=close_volume,
            reason=reason,
        )

    # --- Logging for exit reasons and state ---
    def _log_exit_action(self, action: ExitAction, position: Any, tick: Any) -> None:
        try:
            msg = (
                f"[ExitTrade] EXIT: ticket={action.ticket} symbol={action.symbol} "
                f"side={action.side} volume={action.volume} reason={action.reason} "
                f"pos={position} tick={getattr(tick, 'time', None)}"
            )
            print(msg)
        except Exception:
            pass

    def _log_exit_error(self, ticket: Any, exc: Exception) -> None:
        try:
            print(f"[ExitTrade] ERROR: ticket={ticket} error={exc}")
        except Exception:
            pass

    # --- Graceful handling of MT5/broker errors ---
    def _safe_get_positions(self):
        getter = getattr(self._broker, "get_open_positions", None)
        try:
            if callable(getter):
                return getter()
        except Exception as exc:
            print(f"[ExitTrade] ERROR: get_open_positions failed: {exc}")
        return []

    # -------------------------
    # Tick: protective + (optional) profit exits
    # -------------------------

    def _evaluate_position_on_tick(
        self, position: Any, tick: Any
    ) -> Optional[ExitAction]:
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
            try:
                tick = mt5.symbol_info_tick(symbol)
            except Exception:
                return None
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
                anchor_close=0.0,
                prev_close=0.0,
                closes_seen=0,
            )
            self._state_by_ticket[ticket] = st
        else:
            st.ticks_seen += 1

        # --- FIX: Update favorable/unfavorable bookkeeping BEFORE early-abort ---
        min_fav_pips = float(
            getattr(
                self._config,
                "early_abort_min_fav_pips",
                getattr(Config, "EXIT_EARLY_ABORT_MIN_FAV_PIPS", 1.0),
            )
        )
        pip_price = self._pips_to_price(symbol=symbol, pips=1.0) or 0.0
        if pip_price > 0:
            if side == "buy":
                favorable_move = price - entry
            else:
                favorable_move = entry - price
            favorable_pips = favorable_move / pip_price
            if favorable_pips >= min_fav_pips:
                st.ever_favorable = True
            else:
                if not st.ever_favorable:
                    st.unfavorable_ticks += 1

        # ---- Soft stop loss (MONEY in account currency) with grace ticks ----
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

            if (not st.ever_favorable) and (st.ticks_seen >= n_ticks):
                pip_price = self._pips_to_price(symbol=symbol, pips=1.0) or 0.0
                if pip_price > 0:
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

        # If profit exits should NOT be evaluated on tick, stop here.
        if not bool(getattr(self._config, "profit_exits_on_tick", True)):
            st.prev_price = float(price)
            return None

        # "In profit" threshold for profit exits (per-symbol)
        min_profit_pips = self._get_min_profit_pips(symbol)
        min_profit_price = (
            self._pips_to_price(symbol=symbol, pips=min_profit_pips) or 0.0
        )

        def _net_profit_ok_tick() -> bool:
            if side == "buy":
                return float(price) > (float(entry) + float(min_profit_price))
            return float(price) < (float(entry) - float(min_profit_price))

        # ---- Reversal-in-profit exit (PROFIT; can be HTF-gated) ----
        if bool(getattr(self._config, "exit_on_first_reversal_in_profit", False)):
            prev = float(st.prev_price)
            if bool(getattr(self._config, "treat_flat_as_reversal", False)):
                reversal = (price <= prev) if side == "buy" else (price >= prev)
            else:
                reversal = (price < prev) if side == "buy" else (price > prev)

            if (
                reversal
                and _net_profit_ok_tick()
                and self._htf_allows_profit_exit(symbol=symbol, position_side=side)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="first_reversal_in_profit",
                )

        # Update prev price after reversal check
        st.prev_price = float(price)

        # ---- Anchor update / buffer breach trailing (PROFIT; can be HTF-gated) ----
        eps = self._pips_to_price(symbol=symbol, pips=self._config.eps_pips) or 0.0
        if self._is_favorable_vs_anchor(
            position_side=side, anchor=st.anchor, price=price, eps=eps
        ):
            st.anchor = float(price)
            return None

        if st.ticks_seen < max(1, int(self._config.buffer_start_tick)):
            return None

        buf = self._pips_to_price(
            symbol=symbol, pips=self._dynamic_buffer(symbol, self._config.buffer_pips)
        )
        if buf is None:
            return None

        if side == "buy":
            if (
                price <= (st.anchor - buf)
                and _net_profit_ok_tick()
                and self._htf_allows_profit_exit(symbol=symbol, position_side=side)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="buffer_breach",
                )
        else:
            if (
                price >= (st.anchor + buf)
                and _net_profit_ok_tick()
                and self._htf_allows_profit_exit(symbol=symbol, position_side=side)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="buffer_breach",
                )

        return None

    # -------------------------
    # Candle-close: profit exits
    # -------------------------

    def _evaluate_position_on_candle_close(
        self, position: Any, *, close_price: float
    ) -> Optional[ExitAction]:
        symbol = self._pos_symbol(position)
        side = self._pos_side(position)  # "buy"/"sell"
        ticket = self._pos_ticket(position)
        entry = self._pos_entry(position)
        volume = self._pos_volume(position)

        if not symbol or not side or ticket is None or entry is None or volume is None:
            return None

        st = self._state_by_ticket.get(ticket)
        if st is None:
            st = _PosState(
                anchor=float(close_price),
                prev_price=float(close_price),
                ticks_seen=0,
                ever_favorable=False,
                unfavorable_ticks=0,
                anchor_close=float(close_price),
                prev_close=float(close_price),
                closes_seen=1,
            )
            self._state_by_ticket[ticket] = st
        else:
            if st.closes_seen <= 0:
                st.anchor_close = float(close_price)
                st.prev_close = float(close_price)
                st.closes_seen = 1
            else:
                st.closes_seen += 1

        # "In profit" threshold at candle close (per-symbol)
        min_profit_pips = self._get_min_profit_pips(symbol)
        min_profit_price = (
            self._pips_to_price(symbol=symbol, pips=min_profit_pips) or 0.0
        )

        def _net_profit_ok_close() -> bool:
            if side == "buy":
                return float(close_price) > (float(entry) + float(min_profit_price))
            return float(close_price) < (float(entry) - float(min_profit_price))

        # ---- Candle-close reversal-in-profit (PROFIT; can be HTF-gated) ----
        if bool(getattr(self._config, "exit_on_first_reversal_in_profit", False)):
            prev = float(st.prev_close)
            if bool(getattr(self._config, "treat_flat_as_reversal", False)):
                reversal = (
                    (close_price <= prev) if side == "buy" else (close_price >= prev)
                )
            else:
                reversal = (
                    (close_price < prev) if side == "buy" else (close_price > prev)
                )

            if (
                reversal
                and _net_profit_ok_close()
                and self._htf_allows_profit_exit(symbol=symbol, position_side=side)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="candle_close_reversal_in_profit",
                )

        # Update prev close after reversal check
        st.prev_close = float(close_price)

        # ---- Candle-close anchor/buffer trailing (PROFIT; can be HTF-gated) ----
        eps = self._pips_to_price(symbol=symbol, pips=self._config.eps_pips) or 0.0
        if self._is_favorable_vs_anchor(
            position_side=side,
            anchor=st.anchor_close,
            price=float(close_price),
            eps=float(eps),
        ):
            st.anchor_close = float(close_price)
            return None

        start_n = int(getattr(self._config, "buffer_start_candle", 1) or 1)
        if st.closes_seen < max(1, start_n):
            return None

        buf = self._pips_to_price(
            symbol=symbol, pips=self._dynamic_buffer(symbol, self._config.buffer_pips)
        )
        if buf is None:
            return None

        if side == "buy":
            if (
                float(close_price) <= (float(st.anchor_close) - float(buf))
                and _net_profit_ok_close()
                and self._htf_allows_profit_exit(symbol=symbol, position_side=side)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="candle_close_buffer_breach",
                )
        else:
            if (
                float(close_price) >= (float(st.anchor_close) + float(buf))
                and _net_profit_ok_close()
                and self._htf_allows_profit_exit(symbol=symbol, position_side=side)
            ):
                return self._exit_action(
                    ticket=ticket,
                    symbol=symbol,
                    position_side=side,
                    volume=volume,
                    reason="candle_close_buffer_breach",
                )

        return None

    # -------------------------
    # Helpers: positions & ticks
    # -------------------------

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
        """Try to read floating profit from the position object (MT5 positions expose 'profit')."""
        v = self._get_any(position, ("profit", "pnl", "floating_profit"))
        return float(v) if v not in (None, "") else None

    def _mt5_profit_by_ticket(self, ticket: Any) -> Optional[float]:
        """Fallback if broker position objects don't include profit."""
        if ticket is None:
            return None
        try:
            try:
                positions = mt5.positions_get(ticket=ticket)
            except TypeError:
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
            return float(price) > (float(entry) + float(eps))
        return float(price) < (float(entry) - float(eps))

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

    def _is_favorable_vs_anchor(
        self, *, position_side: str, anchor: float, price: float, eps: float
    ) -> bool:
        a = float(anchor)
        p = float(price)
        e = float(eps or 0.0)
        if position_side == "buy":
            return p > (a + e)
        return p < (a - e)
