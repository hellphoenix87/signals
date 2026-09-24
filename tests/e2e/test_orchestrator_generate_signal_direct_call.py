"""E2E coverage: `SignalOrchestrator._run_entries` drives the *real* signal
generator and passes its *real* output on to the trade executor.

`_run_entries` (app/services/trade_services.py) calls
`signal_generator.generate_signal(snapshot)` directly and hands whatever
buy/sell dicts come back to `trade_executor.process_signal(signals, snapshot)`.

These tests drive a real `SignalOrchestrator` wired with real signal-generator
strategy instances -- `StrongSignalStrategy` and
`NTickConfirmedSignalStrategy` (app/signals/strategies/), no stubs standing in
for the generator -- and prove
that the real `generate_signal` method is genuinely invoked with the real
candles/snapshot, and that its real (not hand-crafted) output is what reaches
the rest of the pipeline.

What's real here vs. mocked: `SignalOrchestrator` and the strategy instances
are real. `trade_executor` is a bare `MagicMock` (it's the downstream consumer
of `generate_signal`'s output, not part of what these tests cover), as is
`collector`. No MT5 import and no real `Broker`/`RiskManager`/`TradeExecutor`
construction happens in this file.
"""

from __future__ import annotations

import datetime
import logging
from unittest.mock import MagicMock

from app.config.settings import Config
from app.services.trade_services import create_orchestrator
from app.signals.strategies.ntick_confirmed_signal_strategy import (
    NTickConfirmedSignalStrategy,
)
from app.signals.strategies.strong_signal_strategy import StrongSignalStrategy

_TEST_LOGGER = logging.getLogger("test-orchestrator-generate-signal-direct-call")


def _always_buy(_candles):
    """A trivial-but-real indicator function: always votes 'buy'.

    Using a custom indicator (rather than the real MACD indicator) keeps the
    signal deterministic without faking the strategy class itself --
    `strategy_factory`/`StrongSignalStrategy` explicitly accept arbitrary
    indicator callables, so this is a realistic, supported construction.
    """
    return "buy"


def _make_strong_signal_strategy() -> StrongSignalStrategy:
    return StrongSignalStrategy(
        indicators={"always_buy": _always_buy},
        logger=_TEST_LOGGER,
        min_candles=1,
        confidence_threshold=0.5,
        config=Config,
    )


def _make_orchestrator(strategy, trade_executor):
    return create_orchestrator(
        collector=MagicMock(),
        signal_generator=strategy,
        trade_executor=trade_executor,
        tick_collector=None,
        exit_trade=None,
        logger=None,
    )


class TestRealStrongSignalStrategyDrivesRealEntries:
    def test_run_entries_calls_generate_signal_directly_and_uses_its_output(self):
        """A real StrongSignalStrategy (built the same way strategy_factory
        wires production code, just with a deterministic indicator) must be
        invoked via its real `generate_signal(snapshot)` method, and its
        real return value -- not a hand-crafted stand-in -- must be what
        reaches `trade_executor.process_signal`.
        """
        strategy = _make_strong_signal_strategy()
        trade_executor = MagicMock()

        orchestrator = _make_orchestrator(strategy, trade_executor)

        candles = [{"symbol": "EURUSD", "close": 1.1000, "time": 1}]

        orchestrator._run_entries(
            snapshot=candles, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        assert trade_executor.process_signal.call_count == 1, (
            "expected the real StrongSignalStrategy.generate_signal(snapshot) "
            "output to reach trade_executor.process_signal exactly once"
        )
        (signals, snapshot), _kwargs = trade_executor.process_signal.call_args
        assert snapshot is candles
        assert signals == [
            {
                "final_signal": "buy",
                "raw_signal": "buy",
                "confidence": 1.0,
                "indicators": {"always_buy": "buy"},
                "symbol": "EURUSD",
            }
        ], (
            "the dicts passed to the trade executor must be exactly what the "
            "real generate_signal() computed -- not a mangled/partial result"
        )

    def test_run_entries_still_forwards_a_hold_signal_for_the_executor_to_filter(self):
        """A real strategy that genuinely produces 'hold' must not result in
        an entry. `_run_entries` forwards every dict it gets and lets
        `TradeExecutor.process_signal` do the buy/sell filtering, so the
        assertion here is on the *content* reaching the executor, proving the
        real (not guessed) return value is what's actually consulted.
        """
        strategy = StrongSignalStrategy(
            indicators={"always_hold": lambda _candles: "hold"},
            logger=_TEST_LOGGER,
            min_candles=1,
            confidence_threshold=0.5,
            config=Config,
        )
        trade_executor = MagicMock()

        orchestrator = _make_orchestrator(strategy, trade_executor)

        candles = [{"symbol": "EURUSD", "close": 1.1000, "time": 1}]
        orchestrator._run_entries(
            snapshot=candles, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        (signals, _snapshot), _kwargs = trade_executor.process_signal.call_args
        assert [s["final_signal"] for s in signals] == ["hold"]


class TestRealNTickConfirmedStrategyOutputIsRespected:
    def test_run_entries_respects_real_hold_output_pending_confirmation(self):
        """NTickConfirmedSignalStrategy.generate_signal returns 'hold' (with
        a 'waiting_for_n_tick_confirmation' reason) on the very candle that
        starts a new pending signal -- it only confirms via on_new_tick(),
        never on the same generate_signal() call. This proves the real
        method was invoked with the real candles (its internal pending-signal
        state changes accordingly) *and* that its real 'hold' output is what
        reached the executor -- not a guessed call silently no-op'ing.
        """
        strategy = NTickConfirmedSignalStrategy(
            _make_strong_signal_strategy(), n_ticks=3
        )
        trade_executor = MagicMock()

        orchestrator = _make_orchestrator(strategy, trade_executor)

        candles = [
            {"symbol": "EURUSD", "close": 1.0500, "time": 1},
            {"symbol": "EURUSD", "close": 1.0600, "time": 2},
        ]

        orchestrator._run_entries(
            snapshot=candles, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        (signals, _snapshot), _kwargs = trade_executor.process_signal.call_args
        assert [s["final_signal"] for s in signals] == ["hold"]

        # The real generate_signal() call must have actually run against our
        # real candles and updated real internal state accordingly.
        assert strategy._waiting is True
        assert strategy._pending_signal == "buy"
        assert strategy._pending_entry_price == 1.0600
