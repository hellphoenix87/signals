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
    trade_executor: Any,
    tick_collector: Optional[Any] = None,
    exit_trade: Optional[Any] = None,
    logger: Optional[Any] = None,
) -> "SignalOrchestrator":
    """Provider that creates and returns a SignalOrchestrator."""
    return SignalOrchestrator(
        collector=collector,
        signal_generator=signal_generator,
        trade_executor=trade_executor,
        tick_collector=tick_collector,
        exit_trade=exit_trade,
        logger=logger,
    )


class SignalOrchestrator:
    """Drives one symbol's signal generation, entry execution, and exits.

    - Candle loop (cadence = `TF_ENTRY`, typically M1): once per new closed
      candle, generates a signal (updating `exit_trade`'s HTF bias), runs
      candle-close profit exits, then hands any buy/sell signal to
      `trade_executor` for entry.
    - Tick path: on every tick, runs protective exits via `exit_trade.on_tick`,
      forwards the tick to `signal_generator.on_new_tick` (only meaningful
      for strategies that implement n-tick confirmation), and executes any
      signal `signal_generator.get_confirmed_signal()` returns.
    """

    def __init__(
        self,
        *,
        collector: Any,
        signal_generator: Any,
        trade_executor: Any,
        tick_collector: Optional[Any] = None,
        exit_trade: Optional[Any] = None,
        logger: Optional[Any] = None,
    ) -> None:
        self.collector = collector
        self.signal_generator = signal_generator
        self.trade_executor = trade_executor

        self.tick_collector = tick_collector
        self.exit_trade = exit_trade
        self.logger = logger

        self._running: bool = False
        self._thread: Optional[threading.Thread] = None

        self._last_closed_time: Optional[datetime] = None
        self._last_signal: Optional[dict] = None
        self._last_tick: Any = None

        self._tf_entry: int = int(
            getattr(
                signal_generator,
                "tf_entry",
                getattr(Config, "TF_ENTRY", mt5.TIMEFRAME_M1),
            )
        )

    def is_running(self) -> bool:
        """Return whether the background candle loop is currently running."""
        return self._running

    def get_latest_signal(self, symbol: Optional[str] = None) -> Optional[dict]:
        """Return the most recently generated signal, if any."""
        return self._last_signal

    def get_tick(self) -> Any:
        """Return the most recently received tick, if any."""
        return self._last_tick

    def start(self) -> None:
        """Start the background candle loop and wire the tick callback (idempotent)."""
        if self._running:
            return

        self.collector.start()
        if self.tick_collector:
            self.tick_collector.start(self._on_tick)

        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="SignalOrchestrator", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background candle loop and tick collection."""
        self._running = False
        if self.tick_collector:
            self.tick_collector.stop()
        self.collector.stop()

    def _on_tick(self, tick: Any) -> None:
        """Run protective exits, forward the tick to n-tick confirmation logic
        (if the signal generator supports it), and execute any confirmed signal."""
        self._last_tick = tick

        if self.exit_trade:
            try:
                actions = self.exit_trade.on_tick(tick)
            except Exception as exc:
                self._log_exception(f"[Orchestrator] exit_trade.on_tick error: {exc!r}")
                actions = []
            if actions:
                self._execute_exit_actions(actions)

        if hasattr(self.signal_generator, "on_new_tick"):
            try:
                price = getattr(tick, "bid", None) or getattr(tick, "last", None)
                spread_points = getattr(tick, "spread", None)
                self.signal_generator.on_new_tick(price, spread_points)
            except Exception as exc:
                self._log_exception(
                    f"[Orchestrator] signal_generator.on_new_tick error: {exc!r}"
                )

        if hasattr(self.signal_generator, "get_confirmed_signal"):
            tick_epoch = getattr(tick, "time", None)
            try:
                candle_frame_time = datetime.fromtimestamp(tick_epoch) if tick_epoch else None
            except Exception:
                candle_frame_time = None
            try:
                sig = self.signal_generator.get_confirmed_signal(candle_frame_time)
            except TypeError:
                sig = self.signal_generator.get_confirmed_signal()
            if sig and (sig.get("final_signal") in ("buy", "sell")):
                self._last_signal = sig
                try:
                    self.trade_executor.process_signal([sig], None)
                except Exception as exc:
                    self._log_exception(
                        f"[Orchestrator] trade_executor.process_signal error: {exc!r}"
                    )

    def _run(self) -> None:
        """Background loop: on each new closed candle, generate a signal,
        run candle-close profit exits, and execute any entry."""
        poll_sleep = min(float(getattr(self.collector, "interval", 1) or 1), 0.05)
        symbol = self.collector.symbol

        while self._running:
            try:
                snapshot = self._get_latest_candles()
                did_work = False

                if snapshot:
                    entry_candles = self._extract_tf_candles(snapshot, self._tf_entry)
                    closed_candle = self._last_closed_candle(entry_candles)
                    closed_time = self._candle_time(closed_candle)

                    if closed_time is not None:
                        if self._last_closed_time is None:
                            self._last_closed_time = closed_time
                        elif closed_time > self._last_closed_time:
                            self._last_closed_time = closed_time
                            did_work = True

                            self._run_entries(snapshot=snapshot, asof=closed_time)
                            self._run_candle_close_profit_exits(
                                symbol=symbol,
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

        close_px = closed_candle.get("close") if isinstance(closed_candle, dict) else None
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
        """Generate a signal for this candle close, update HTF bias, and
        hand any buy/sell signal to `trade_executor`."""
        try:
            sig_out = self.signal_generator.generate_signal(snapshot)
        except Exception as exc:
            self._log_exception(f"[Orchestrator] signal generator call failed: {exc!r}")
            return

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

        self._last_signal = signals[0]
        self._log(
            f"[Orchestrator] signals={len(signals)} asof={asof.isoformat()} sample={signals[0]}"
        )

        if self.exit_trade:
            for sig in signals:
                symbol = sig.get("symbol")
                if not symbol:
                    continue
                try:
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

        try:
            self.trade_executor.process_signal(signals, snapshot)
        except Exception as exc:
            self._log_exception(
                f"[Orchestrator] trade_executor.process_signal error: {exc!r}"
            )

    def _execute_exit_actions(self, actions: List[Any]) -> None:
        for a in actions:
            try:
                self.trade_executor.execute_exit(a)
            except Exception as exc:
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
                self._log_exception(
                    f"[Orchestrator] exit execution failed for ticket={ticket} symbol={symbol}: {exc!r}"
                )

    def _get_latest_candles(self) -> Any:
        try:
            return self.collector.get_latest_candles()
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

    def _candle_time(self, candle: Optional[dict]) -> Optional[datetime]:
        if not candle or not isinstance(candle, dict):
            return None

        t = candle.get("time") or candle.get("timestamp") or candle.get("time_msc")

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
