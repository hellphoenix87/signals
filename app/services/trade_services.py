from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import MetaTrader5 as mt5

from app.config.settings import Config


def create_orchestrator(
    collector: Any,
    signal_generator: Any,
    broker: Optional[Any] = None,
    trading_service: Optional[Any] = None,
    tick_collector: Optional[Any] = None,
    exit_trade: Optional[Any] = None,
    enter_trade: Optional[Any] = None,
    logger: Optional[Any] = None,
) -> "SignalOrchestrator":
    """
    Provider that creates and returns a SignalOrchestrator.
    """
    return SignalOrchestrator(
        collector=collector,
        signal_generator=signal_generator,
        broker=broker,
        trading_service=trading_service,
        tick_collector=tick_collector,
        exit_trade=exit_trade,
        enter_trade=enter_trade,
        logger=logger,
    )


class SignalOrchestrator:
    """
    Signal orchestrator (HYBRID exits):

    - Candle loop (cadence = TF_ENTRY, typically M1):
        * once per NEW CLOSED M1 candle (per symbol):
            1) generate signals (and update HTF bias into exit_trade)
            2) run candle-close PROFIT exits (ExitTrade.on_candle_close)
            3) execute entries (trading_service OR enter_trade/broker fallback)

    - Tick path:
        * on every tick: run protective exits (ExitTrade.on_tick)
        * on every tick: forward tick to signal_generator.on_new_tick (for n-tick logic)
    """

    def __init__(
        self,
        *,
        collector: Any,
        signal_generator: Any,
        broker: Optional[Any] = None,
        trading_service: Optional[Any] = None,
        tick_collector: Optional[Any] = None,
        exit_trade: Optional[Any] = None,
        enter_trade: Optional[Any] = None,
        logger: Optional[Any] = None,
        pending_entries: Optional[dict[str, dict]] = None,
    ) -> None:
        self.collector = collector
        self.signal_generator = signal_generator
        self.broker = broker
        self.trading_service = trading_service

        self.tick_collector = tick_collector
        self.exit_trade = exit_trade
        self.enter_trade = enter_trade
        self.logger = logger

        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

        self._last_closed_time_by_symbol: Dict[str, datetime] = {}

        self._tf_entry: int = int(
            getattr(
                signal_generator,
                "tf_entry",
                getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1),
            )
        )
        self._tf_confirm: int = int(
            getattr(
                signal_generator,
                "tf_confirm",
                getattr(Config, "TF_CONFIRM", mt5.TIMEFRAME_M5),
            )
        )
        self._tf_bias: int = int(
            getattr(
                signal_generator,
                "tf_bias",
                getattr(Config, "TF_BIAS", mt5.TIMEFRAME_M15),
            )
        )
        self.pending_entries: Dict[str, dict] = pending_entries or {}

    # -------------------------
    # Lifecycle
    # -------------------------

    def start(self) -> None:
        if self._running:
            return

        self._safe_call(self.collector, "start")
        self._wire_tick_callback()

        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="SignalOrchestrator", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._safe_call(self.tick_collector, "stop")
        self._safe_call(self.collector, "stop")

    # -------------------------
    # Tick path (protective exits + n-tick logic)
    # -------------------------

    def _wire_tick_callback(self) -> None:
        if not self.tick_collector:
            self._log("[Orchestrator] tick_collector is None (tick exits disabled)")
            return

        self._log(
            f"[Orchestrator] wiring tick_collector={type(self.tick_collector).__name__}"
        )

        set_cb = getattr(self.tick_collector, "set_callback", None)
        start = getattr(self.tick_collector, "start", None)
        self._log(
            f"[Orchestrator] tick_collector methods: set_callback_callable={callable(set_cb)} start_callable={callable(start)}"
        )

        if callable(set_cb):
            try:
                set_cb(self._on_tick)
                self._log("[Orchestrator] tick_collector.set_callback OK")
            except Exception as exc:
                self._log_exception(
                    f"[Orchestrator] tick_collector.set_callback error: {exc!r}"
                )

        if callable(start):
            try:
                start(self._on_tick)
                self._log("[Orchestrator] tick_collector.start(cb) OK")
            except TypeError:
                try:
                    start()
                    self._log("[Orchestrator] tick_collector.start() OK")
                except Exception as exc:
                    self._log_exception(
                        f"[Orchestrator] tick_collector.start() error: {exc!r}"
                    )
            except Exception as exc:
                self._log_exception(
                    f"[Orchestrator] tick_collector.start error: {exc!r}"
                )

    def _on_tick(self, tick: Any) -> None:
        # 1. Run protective exits
        if self.exit_trade:
            try:
                actions = self.exit_trade.on_tick(tick)
            except Exception as exc:
                self._log_exception(f"[Orchestrator] exit_trade.on_tick error: {exc!r}")
                actions = []
            if actions:
                self._execute_exit_actions(actions)

        # 2. Forward tick to n-tick confirmation logic (signal_generator handles tick logic)
        if hasattr(self.signal_generator, "on_new_tick"):
            try:
                price = getattr(tick, "bid", None) or getattr(tick, "last", None)
                spread_points = getattr(tick, "spread", None)
                self.signal_generator.on_new_tick(price, spread_points)
            except Exception as exc:
                self._log_exception(
                    f"[Orchestrator] signal_generator.on_new_tick error: {exc!r}"
                )
        # Prefer trading_service for entries if available
        if hasattr(self.signal_generator, "get_confirmed_signal"):
            sig = self.signal_generator.get_confirmed_signal()
            if sig and (sig.get("final_signal") in ("buy", "sell")):
                if self.trading_service and hasattr(
                    self.trading_service, "process_signal"
                ):
                    try:
                        self.trading_service.process_signal([sig], None)
                    except Exception as exc:
                        self._log_exception(
                            f"[Orchestrator] trading_service.process_signal error: {exc!r}"
                        )
                elif self.enter_trade:
                    fn = (
                        getattr(self.enter_trade, "on_signal", None)
                        or getattr(self.enter_trade, "execute", None)
                        or getattr(self.enter_trade, "enter", None)
                    )
                    if callable(fn):
                        try:
                            fn(sig)
                        except Exception as exc:
                            self._log_exception(
                                f"[Orchestrator] enter_trade execution error: {exc!r}"
                            )
                elif self.broker:
                    self.broker.place_market_order(
                        symbol=sig["symbol"], side=sig["final_signal"]
                    )

        # DO NOT call generate_signal here! Only on new candle.

    # -------------------------
    # Candle loop (entries + candle-close profit exits)
    # -------------------------

    def _run(self) -> None:
        poll_sleep = float(getattr(self.collector, "interval", 1) or 1)

        while self._running:
            try:
                symbols = self._symbols_to_process()
                did_work = False

                for symbol in symbols:
                    snapshot = self._get_latest_candles(symbol=symbol)
                    if not snapshot:
                        continue

                    entry_candles = self._extract_tf_candles(snapshot, self._tf_entry)
                    closed_candle = self._last_closed_candle(entry_candles)
                    closed_time = self._candle_time(closed_candle)
                    if closed_time is None:
                        continue

                    sym = self._resolve_symbol_from_candle_or_fallback(
                        closed_candle, fallback=symbol
                    )
                    if not sym:
                        continue

                    # Trigger once per NEW closed candle
                    last_t = self._last_closed_time_by_symbol.get(sym)
                    if last_t is None:
                        # baseline only (avoid firing immediately on startup)
                        self._last_closed_time_by_symbol[sym] = closed_time
                        continue
                    if closed_time <= last_t:
                        continue

                    self._last_closed_time_by_symbol[sym] = closed_time
                    did_work = True

                    # 1) signals first (this updates bias via exit_trade.update_bias)
                    self._run_entries(snapshot=snapshot, asof=closed_time)

                    # 2) then candle-close profit exits (now bias is current)
                    self._run_candle_close_profit_exits(
                        symbol=sym,
                        closed_candle=closed_candle,
                        closed_time=closed_time,
                    )

                time.sleep(0.1 if did_work else poll_sleep)

            except Exception as exc:
                self._log_exception(f"[Orchestrator] loop error: {exc!r}")
                time.sleep(1)

    def _run_candle_close_profit_exits(
        self,
        *,
        symbol: str,
        closed_candle: Optional[dict],
        closed_time: datetime,
    ) -> None:
        if not self.exit_trade:
            return

        on_close = getattr(self.exit_trade, "on_candle_close", None)
        if not callable(on_close):
            return

        close_px = None
        if isinstance(closed_candle, dict):
            close_px = closed_candle.get("close")

        try:
            close_px_f = float(close_px) if close_px is not None else None
        except Exception:
            close_px_f = None

        if close_px_f is None:
            return

        try:
            actions = on_close(
                symbol=str(symbol),
                close_price=float(close_px_f),
                asof_epoch=float(closed_time.timestamp()),
            )
        except Exception as exc:
            self._log_exception(
                f"[Orchestrator] exit_trade.on_candle_close error: {exc!r}"
            )
            return

        if actions:
            self._execute_exit_actions(actions)

    def _run_entries(self, *, snapshot: Any, asof: datetime) -> None:
        sg = self.signal_generator

        # Accept multiple generator APIs (backwards/forwards compatible)
        gen = (
            getattr(sg, "generate_signal", None)
            or getattr(sg, "generate_signals", None)
            or getattr(sg, "__call__", None)
        )
        if not callable(gen):
            self._log(
                f"[Orchestrator] signal_generator has no callable generate_signal()/generate_signals()/__call__: {type(sg).__name__}"
            )
            return

        # Call the generator with best-effort signature matching
        try:
            try:
                sig_out = gen(snapshot)
            except TypeError:
                try:
                    sig_out = gen(candles_snapshot=snapshot)
                except TypeError:
                    try:
                        sig_out = gen()
                    except TypeError:
                        # Last fallback: some generators use account_balance; try to fetch from broker if possible
                        bal = None
                        get_bal = (
                            getattr(self.broker, "get_account_balance", None)
                            if self.broker
                            else None
                        )
                        if callable(get_bal):
                            try:
                                bal = float(get_bal())
                            except Exception:
                                bal = None
                        if bal is None:
                            raise
                        sig_out = gen(account_balance=bal)
        except Exception as exc:
            self._log_exception(f"[Orchestrator] signal generator call failed: {exc!r}")
            return

        # Normalize into list[dict]
        signals: List[dict]
        if isinstance(sig_out, list):
            signals = [s for s in sig_out if isinstance(s, dict)]
        elif isinstance(sig_out, dict):
            signals = [sig_out]
        else:
            self._log(
                f"[Orchestrator] signal generator returned unsupported type: {type(sig_out).__name__}"
            )
            return

        if not signals:
            self._log("[Orchestrator] signal generator returned 0 dict signals")
            return

        self._log(
            f"[Orchestrator] signals={len(signals)} asof={asof.isoformat()} sample={signals[0]}"
        )

        # Update HTF context for profit-exit gating (best-effort)
        if self.exit_trade:
            for sig in signals:
                try:
                    symbol = sig.get("symbol")
                    if not symbol:
                        continue
                    self.exit_trade.update_bias(
                        str(symbol),
                        m5=sig.get("m5_confirm"),
                        m15=sig.get("m15_bias"),
                        asof_epoch=float(asof.timestamp()),
                    )
                except Exception as exc:
                    self._log_exception(
                        f"[Orchestrator] exit_trade.update_bias error: {exc!r}"
                    )

        # If trading_service exists, let it handle execution
        if self.trading_service:
            proc = getattr(self.trading_service, "process_signal", None)
            if callable(proc):
                try:
                    proc(signals, snapshot)
                except Exception as exc:
                    self._log_exception(
                        f"[Orchestrator] trading_service.process_signal error: {exc!r}"
                    )
            return

        # Otherwise execute here (enter_trade preferred, broker fallback)
        if not self.enter_trade and not self.broker:
            self._log("[Orchestrator] No enter_trade and no broker; skipping execution")
            return

        for sig in signals:
            symbol = sig.get("symbol")
            final_signal = (sig.get("final_signal") or "hold").lower()
            pullback_completed = sig.get("pullback_completed", True)
            if not symbol or final_signal not in ("buy", "sell"):
                continue

            # --- Pending entry logic ---
            if final_signal in ("buy", "sell") and not pullback_completed:
                self.pending_entries[symbol] = sig
                continue  # Do not execute trade yet

            if self.enter_trade:
                fn = (
                    getattr(self.enter_trade, "on_signal", None)
                    or getattr(self.enter_trade, "execute", None)
                    or getattr(self.enter_trade, "enter", None)
                )
                if callable(fn):
                    try:
                        fn(sig)
                        continue
                    except Exception as exc:
                        self._log_exception(
                            f"[Orchestrator] enter_trade execution error: {exc!r}"
                        )

            if not self.broker:
                continue

            place = (
                getattr(self.broker, "place_market_order", None)
                or getattr(self.broker, "place_order", None)
                or getattr(self.broker, "open_position", None)
            )
            if callable(place):
                try:
                    place(symbol=str(symbol), side=final_signal)
                except TypeError:
                    try:
                        place(symbol=str(symbol), signal=final_signal)
                    except Exception as exc:
                        self._log_exception(
                            f"[Orchestrator] broker.place_* TypeError fallback failed: {exc!r}"
                        )
                except Exception as exc:
                    self._log_exception(f"[Orchestrator] broker.place_* error: {exc!r}")

    # -------------------------
    # Broker execution helpers
    # -------------------------

    def _execute_exit_actions(self, actions: List[Any]) -> None:
        # Prefer trading_service (TradeExecutor) for exits if available
        if self.trading_service and hasattr(self.trading_service, "execute_exit"):
            for a in actions:
                self.trading_service.execute_exit(a)
            return

        # Fallback: call broker directly
        if not self.broker:
            return

        for a in actions:
            ticket = (
                getattr(a, "ticket", None)
                if not isinstance(a, dict)
                else a.get("ticket")
            )
            symbol = (
                getattr(a, "symbol", None)
                if not isinstance(a, dict)
                else a.get("symbol")
            )
            side = (
                getattr(a, "side", None) if not isinstance(a, dict) else a.get("side")
            )
            volume = (
                getattr(a, "volume", None)
                if not isinstance(a, dict)
                else a.get("volume")
            )

            closer = (
                getattr(self.broker, "close_position", None)
                or getattr(self.broker, "close_trade", None)
                or getattr(self.broker, "close_order", None)
            )
            if not callable(closer):
                continue

            try:
                if ticket is not None:
                    closer(ticket=ticket, symbol=symbol, side=side, volume=volume)
                else:
                    closer(symbol=symbol, side=side, volume=volume)
            except TypeError:
                try:
                    closer(ticket, symbol, side, volume)
                except Exception:
                    pass
            except Exception:
                pass

    # -------------------------
    # Candle snapshot helpers
    # -------------------------

    def _symbols_to_process(self) -> List[str]:
        sym = getattr(self.collector, "symbol", None)
        if sym:
            return [str(sym)]

        syms = getattr(Config, "SYMBOLS", None)
        if isinstance(syms, list) and syms:
            max_syms = int(getattr(Config, "MAX_SYMBOLS", len(syms)) or len(syms))
            return [str(s) for s in syms[:max_syms] if s]
        return []

    def _get_latest_candles(self, *, symbol: Optional[str]) -> Any:
        fn = getattr(self.collector, "get_latest_candles", None) or getattr(
            self.collector, "get_candles", None
        )
        if not callable(fn):
            return None
        try:
            try:
                return fn(symbol=symbol) if symbol else fn()
            except TypeError:
                return fn()
        except Exception:
            return None

    def _extract_tf_candles(self, snapshot: Any, tf: int) -> List[dict]:
        if isinstance(snapshot, dict):
            v = snapshot.get(int(tf), []) or []
            return v if isinstance(v, list) else []
        if isinstance(snapshot, list):
            return snapshot
        return []

    def _last_closed_candle(self, candles: List[dict]) -> Optional[dict]:
        if not candles:
            return None

        for c in reversed(candles):
            if isinstance(c, dict) and self._is_candle_closed(c):
                return c

        return candles[-2] if len(candles) >= 2 else candles[-1]

    def _is_candle_closed(self, candle: dict) -> bool:
        for k in ("is_closed", "closed", "complete", "is_complete"):
            if k in candle:
                return bool(candle.get(k))
        return True

    def _resolve_symbol_from_candle_or_fallback(
        self, candle: Optional[dict], fallback: Optional[str]
    ) -> Optional[str]:
        if isinstance(candle, dict):
            s = candle.get("symbol")
            if s:
                return str(s)
        if fallback:
            return str(fallback)
        return None

    def _candle_time(self, candle: Optional[dict]) -> Optional[datetime]:
        if not candle or not isinstance(candle, dict):
            return None

        t = candle.get("time")
        if t is None:
            t = candle.get("timestamp")
        if t is None:
            t = candle.get("time_msc")

        if isinstance(t, datetime):
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

        try:
            if isinstance(t, (int, float)):
                if float(t) > 10_000_000_000:
                    return datetime.fromtimestamp(float(t) / 1000.0, tz=timezone.utc)
                return datetime.fromtimestamp(float(t), tz=timezone.utc)
        except Exception:
            return None

        try:
            if isinstance(t, str) and t:
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

        return None

    # -------------------------
    # Misc helpers
    # -------------------------

    def _safe_call(self, obj: Any, method_name: str) -> None:
        if not obj:
            return
        fn = getattr(obj, method_name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        try:
            if self.logger and hasattr(self.logger, "info"):
                self.logger.info(msg)
            else:
                print(msg)
        except Exception:
            pass

    def _log_exception(self, msg: str) -> None:
        try:
            if self.logger and hasattr(self.logger, "exception"):
                self.logger.exception(msg)
            else:
                print(msg)
        except Exception:
            pass
