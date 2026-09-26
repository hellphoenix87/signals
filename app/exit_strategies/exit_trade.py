from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Optional

import time
import threading
import MetaTrader5 as mt5

from app.config.settings import Config
from app.exit_strategies.exit_shared import (
    ExitAction,
    PosState,
    pos_symbol,
    pos_ticket,
)
from app.exit_strategies.managers.profit import ProfitExitManager
from app.exit_strategies.managers.loss import LossExitManager


def _config_or_default(name: str, default: Any) -> Any:
    """`Config.<name>`, falling back to `default` only when the setting is
    missing or None -- NOT when it is 0. `getattr(...) or default` silently
    turned a deliberate 0 into the default for settings where 0 is meaningful:
    a $0 post-BE cap (exit at breakeven) became $5, `be_arming_ticks = 0`
    (timeout disabled, per LossExitManager) became 20, and a $0 trail floor
    became $2.
    """
    value = getattr(Config, name, None)
    return default if value is None else value


def create_exit_trade(
    broker: Any, risk_manager: Any, config: Optional["ExitTradeConfig"] = None
) -> "ExitTrade":
    """Provider for DI wiring."""
    return ExitTrade(broker=broker, risk_manager=risk_manager, config=config)


@dataclass(frozen=True)
class ExitTradeConfig:
    """
    Hybrid exit configuration, grouped by concern: trailing buffer, tick-only
    noisy rules (normally off), soft-SL money/price/pips controls, early-abort
    (a spread-safe alternative to "exit on first tick not favorable"),
    break-even arming, higher-timeframe gating (blocks profit-taking exits
    only -- protective exits are never gated), and hybrid tick-vs-candle-close
    mode switches.

    Tick-driven (protective):
      - optional "exit on first tick not favorable" (usually OFF)
      - soft SL by money/price/pips
      - early-abort (if never favorable after N ticks and adverse >= X pips)

    Profit-taking:
      - either tick-based OR candle-close based (recommended with MTF entries)
      - reversal-in-profit + anchor/buffer trailing
      - optionally HTF-gated (blocks profit exits while HTF supports the position)
    """

    buffer_pips: float = float(getattr(Config, "EXIT_BUFFER_PIPS", 0.5) or 0.5)
    buffer_start_tick: int = int(getattr(Config, "EXIT_BUFFER_START_TICK", 3) or 3)
    buffer_start_candle: int = int(getattr(Config, "EXIT_BUFFER_START_CANDLE", 2) or 2)

    trail_gap_floor_money: float = float(_config_or_default("EXIT_TRAIL_GAP_FLOOR_MONEY", 2.0))
    trail_gap_pct: float = float(getattr(Config, "EXIT_TRAIL_GAP_PCT", 0.6) or 0.6)

    staircase_trail_enabled: bool = bool(
        getattr(Config, "EXIT_STAIRCASE_TRAIL_ENABLED", False)
    )
    staircase_tier_width: float = float(
        getattr(Config, "EXIT_STAIRCASE_TIER_WIDTH", 2.0) or 2.0
    )
    # 0.0 is a legitimate configured value meaning "same as tier width", so it
    # is resolved in ProfitExitManager rather than coerced away by `or` here.
    staircase_first_tier: float = float(
        getattr(Config, "EXIT_STAIRCASE_FIRST_TIER", 0.0) or 0.0
    )

    eps_pips: float = float(getattr(Config, "EXIT_EPS_PIPS", 0.0) or 0.0)

    exit_on_first_tick_not_favorable: bool = bool(
        getattr(Config, "EXIT_ON_FIRST_TICK_NOT_FAVORABLE", False)
    )

    exit_on_first_profit_tick: bool = False

    exit_on_first_reversal_in_profit: bool = bool(
        getattr(Config, "EXIT_ON_FIRST_REVERSAL_IN_PROFIT", True)
    )
    treat_flat_as_reversal: bool = bool(
        getattr(Config, "EXIT_TREAT_FLAT_AS_REVERSAL", False)
    )

    max_loss_money: float = float(getattr(Config, "EXIT_MAX_LOSS_MONEY", 0.0) or 0.0)
    max_loss_price: float = float(getattr(Config, "EXIT_MAX_LOSS_PRICE", 0.0) or 0.0)
    max_loss_pips: float = float(getattr(Config, "EXIT_MAX_LOSS_PIPS", 0.0) or 0.0)

    post_be_loss_cap_money: float = float(_config_or_default("EXIT_POST_BE_LOSS_CAP_MONEY", 5.0))

    min_profit_pips: float = float(getattr(Config, "EXIT_MIN_PROFIT_PIPS", 0.0) or 0.0)

    early_abort_enabled: bool = bool(getattr(Config, "EXIT_EARLY_ABORT_ENABLED", False))
    early_abort_ticks: int = int(getattr(Config, "EXIT_EARLY_ABORT_TICKS", 0) or 0)
    early_abort_loss_pips: float = float(
        getattr(Config, "EXIT_EARLY_ABORT_LOSS_PIPS", 0.0) or 0.0
    )

    soft_sl_money_grace_ticks: int = int(
        getattr(Config, "EXIT_SOFT_SL_MONEY_GRACE_TICKS", 0) or 0
    )

    be_arming_ticks: int = int(_config_or_default("EXIT_BE_ARMING_TICKS", 20))

    htf_filter_enabled: bool = bool(getattr(Config, "EXIT_HTF_FILTER_ENABLED", False))
    htf_stale_seconds: int = int(getattr(Config, "EXIT_HTF_STALE_SECONDS", 180) or 180)
    htf_use_m15: bool = bool(getattr(Config, "EXIT_HTF_USE_M15", True))
    htf_use_m5: bool = bool(getattr(Config, "EXIT_HTF_USE_M5", True))

    profit_exits_on_tick: bool = bool(
        getattr(Config, "EXIT_PROFIT_EXITS_ON_TICK", True)
    )
    profit_exits_on_candle_close: bool = bool(
        getattr(Config, "EXIT_PROFIT_EXITS_ON_CANDLE_CLOSE", False)
    )

    @classmethod
    def for_config(cls, cfg: Any, **overrides: Any) -> "ExitTradeConfig":
        """The exit config for a per-symbol config class (`app.config.symbols`).

        Every field whose setting `cfg` overrides relative to `Config` takes
        `cfg`'s value; all others keep their defaults above. `overrides` (e.g.
        backtest CLI flags) are applied last. For `cfg is Config` this is
        exactly `ExitTradeConfig(**overrides)`.
        """
        changes: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            name = f.name.upper() if f.name.startswith("exit_") else f"EXIT_{f.name.upper()}"
            missing = object()
            value = getattr(cfg, name, missing)
            if value is missing or value is None or value == getattr(Config, name, missing):
                continue
            changes[f.name] = type(f.default)(value)
        changes.update(overrides)
        return cls(**changes)


class ExitTrade:
    """Composes `LossExitManager` (protective) and `ProfitExitManager`
    (profit-taking) per open position, on both the tick path (`on_tick`) and
    the candle-close path (`on_candle_close`)."""

    def __init__(
        self,
        broker: Any,
        risk_manager: Any,
        config: Optional[ExitTradeConfig] = None,
    ):
        self._broker = broker
        self._risk_manager = risk_manager
        self._config = config or ExitTradeConfig()
        self._state_by_ticket: dict[Any, PosState] = {}
        self._bias_by_symbol: dict[str, dict[str, Any]] = {}
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

        self._profit_manager = ProfitExitManager(
            config=self._config,
            broker=self._broker,
            get_min_profit_pips=self._get_min_profit_pips,
            dynamic_buffer=self._dynamic_buffer,
            htf_allows_profit_exit=self._htf_allows_profit_exit,
            pips_to_price=self._pips_to_price,
            is_favorable_vs_anchor=self._is_favorable_vs_anchor,
            exit_action=self._exit_action,
        )
        self._loss_manager = LossExitManager(
            config=self._config,
            broker=self._broker,
            risk_manager=self._risk_manager,
            get_min_profit_pips=self._get_min_profit_pips,
            pips_to_price=self._pips_to_price,
            exit_action=self._exit_action,
        )

    def update_bias(
        self,
        symbol: str,
        *,
        m5: Optional[str] = None,
        m15: Optional[str] = None,
        asof_epoch: Optional[float] = None,
    ) -> None:
        """Record the latest M5/M15 bias for `symbol`, read by `_htf_allows_profit_exit`."""
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
        """Return whether HTF bias permits a profit-taking exit for `position_side`.

        Defaults to permissive (`True`) when gating is disabled, no bias has
        been recorded yet, or the recorded bias is stale.
        """
        if not bool(getattr(self._config, "htf_filter_enabled", False)):
            return True
        info = self._bias_by_symbol.get(str(symbol))
        if not info:
            return True
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
        if use_m15 and m15 == supportive:
            return False
        if use_m15 and m15 == opposing:
            return True
        if use_m5:
            return m5 == opposing
        return True

    def on_tick(self, tick: Any) -> list[ExitAction]:
        """Check every open position for a protective (loss) exit, then --
        if enabled -- a profit-taking exit, and return any actions found."""
        positions = self._safe_get_positions()
        if not positions:
            self._state_by_ticket.clear()
            return []
        open_tickets: set[Any] = set()
        actions: list[ExitAction] = []
        for pos in positions:

            ticket = pos_ticket(pos)

            if ticket is None:
                continue
            open_tickets.add(ticket)
            if not self._should_exit(ticket):
                continue
            state = self._state_by_ticket.setdefault(
                ticket, PosState(anchor=0.0, prev_price=0.0)
            )
            loss_action = self._loss_manager.check_exit_on_tick(pos, tick, state)
            if loss_action:
                self._log_exit_action(loss_action, pos, tick)
                actions.append(loss_action)
                continue
            if getattr(self._config, "profit_exits_on_tick", False):
                profit_action = self._profit_manager.check_exit_on_tick(
                    pos, tick, state
                )
                if profit_action:
                    self._log_exit_action(profit_action, pos, tick)
                    actions.append(profit_action)
        self._prune_states(open_tickets)
        return actions

    def on_candle_close(
        self, *, symbol: str, close_price: float, asof_epoch: Optional[float] = None
    ) -> list[ExitAction]:
        """Check every open position for `symbol` for an HTF-gated
        profit-taking exit at this candle's close price."""
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
            if str(pos_symbol(pos) or "") != str(symbol):
                continue
            ticket = pos_ticket(pos)
            if ticket is None or not self._should_exit(ticket):
                continue
            state = self._state_by_ticket.setdefault(
                ticket, PosState(anchor=0.0, prev_price=0.0)
            )
            profit_action = self._profit_manager.check_exit_on_candle_close(
                pos, close_px, state
            )
            if profit_action:
                self._log_exit_action(profit_action, pos, None)
                actions.append(profit_action)
        return actions

    def _should_exit(self, ticket: Any, cooldown: Optional[float] = None) -> bool:
        """Per-ticket cooldown gate: `True` (and resets the cooldown clock)
        at most once per `cooldown` seconds for the same ticket."""
        cooldown = cooldown if cooldown is not None else self._exit_cooldown
        now = time.time()
        last = self._last_exit_time.get(ticket, 0)
        if now - last < cooldown:
            return False
        self._last_exit_time[ticket] = now
        return True

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

    def _get_min_profit_pips(self, symbol: str) -> float:
        if symbol in self._min_profit_pips_by_symbol:
            return float(self._min_profit_pips_by_symbol[symbol])
        return float(getattr(self._config, "min_profit_pips", 0.0) or 0.0)

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

    def _safe_get_positions(self):
        getter = getattr(self._broker, "get_open_positions", None)
        try:
            if callable(getter):
                return getter()
        except Exception as exc:
            print(f"[ExitTrade] ERROR: get_open_positions failed: {exc}")
        return []

    def _prune_states(self, open_tickets: set[Any]) -> None:
        for ticket in list(self._state_by_ticket.keys()):
            if ticket not in open_tickets:
                del self._state_by_ticket[ticket]

    def _pos_entry(self, position: Any) -> Optional[float]:
        v = self._get_any(
            position, ("price_open", "open_price", "entry_price", "price")
        )
        return float(v) if v not in (None, "") else None

    def _pos_volume(self, position: Any) -> Optional[float]:
        v = self._get_any(position, ("volume", "lots", "qty", "quantity"))
        return float(v) if v not in (None, "") else None

    def _pos_profit(self, position: Any) -> Optional[float]:
        v = self._get_any(position, ("profit", "pnl", "floating_profit"))
        return float(v) if v not in (None, "") else None

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
