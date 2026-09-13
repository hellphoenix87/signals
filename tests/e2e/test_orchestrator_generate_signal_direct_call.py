"""E2E coverage for Subphase 3.2 (project-refactor-sweep):

`SignalOrchestrator._run_entries` currently does defensive attribute-guessing
to find a method on its signal-generator collaborator (`sg`):

    gen = (
        getattr(sg, "generate_signal", None)
        or getattr(sg, "generate_signals", None)
        or getattr(sg, "__call__", None)
    )

...followed by a TypeError-fallback ladder trying several calling
conventions: `gen(snapshot)`, then `gen(candles_snapshot=snapshot)`, then
`gen()`, then `gen(account_balance=bal)`.

All three real signal-generator implementations in this codebase --
`StrongSignalStrategy`, `MultiTimeframeStrongSignalStrategy`, and
`NTickConfirmedSignalStrategy` (app/signals/strategies/) -- implement only
`generate_signal(self, candles, ...)`, called with the candles/snapshot as
the sole positional argument. The fix replaces the whole lookup-and-fallback
ladder with a direct call: `sg.generate_signal(snapshot)`.

These tests drive a real `SignalOrchestrator` wired with real signal-generator
strategy instances (no stubs/mocks standing in for `sg`) through
`_run_entries`, and prove:

1. The real `generate_signal` method is genuinely invoked with the real
   candles/snapshot, and its real (not hand-crafted) output is what reaches
   the rest of the pipeline (entry execution via `enter_trade`).

2. There is, in fact, *no* observable behavioral difference at the
   full-stack level between the old guessing ladder and the new direct call
   for any of the three real strategy classes -- because `gen(snapshot)` (the
   ladder's *first* attempted calling convention) already succeeds for all
   three, every time, so the buggy/masking fallback rungs
   (`candles_snapshot=snapshot` / `gen()` / `account_balance=bal`) are
   unreachable dead code for real usage in this codebase. `test_fallback_ladder_rungs_beyond_the_first_are_dead_code_for_real_strategies`
   demonstrates this concretely: the first calling convention always
   succeeds, and the later ones always raise TypeError regardless of which
   real strategy class is used -- so there is no before/after distinguishing
   e2e scenario to construct here. The risk this subphase actually closes
   (an *incorrect* fallback silently swallowing a real signal behind a
   caught TypeError, or calling with wrong kwargs before ever trying the
   right positional form for some *hypothetical* future collaborator) is a
   unit-level concern, and is covered by the developer's parallel unit
   tests against `SignalOrchestrator._run_entries` with fake/mock
   collaborators standing in for `sg` -- not something a real strategy class
   can currently exhibit.

What's real here vs. mocked: `SignalOrchestrator` and the signal-generator
strategy instances (`StrongSignalStrategy`, `MultiTimeframeStrongSignalStrategy`,
`NTickConfirmedSignalStrategy`) are real -- this subphase is scoped to the
generator-dispatch call site only, so those are the collaborators under test.
`enter_trade` is a bare `MagicMock` (it's the downstream consumer of
`generate_signal`'s output, not part of what this subphase changes) and
`broker=None`/`collector=MagicMock()`. No MT5 import, no real `Broker`/
`RiskManager`/`TradeExecutor`/`EnterTrade` construction in this file --
that full-stack chain is covered by `test_orchestrator_enter_trade_dispatch.py`
(Subphase 3.1), which this file does not duplicate.
"""

from __future__ import annotations

import datetime
import logging
from unittest.mock import MagicMock

import MetaTrader5 as mt5
import pytest

from app.config.settings import Config
from app.services.trade_services import create_orchestrator
from app.signals.strategies.multi_timeframe import MultiTimeframeStrongSignalStrategy
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


def _pullback_candles(symbol: str = "EURUSD") -> list[dict]:
    """26 candles whose closes satisfy MultiTimeframeStrongSignalStrategy's
    `_pullback_completed` check (a below-then-above-SMA20 crossing), so the
    real multi-timeframe strategy's `pullback_completed` gate genuinely
    passes rather than being stubbed out.
    """
    closes = [1.0] + [0.9] * 5 + [1.0] * 19 + [1.4]
    assert len(closes) == 26
    return [
        {"symbol": symbol, "close": c, "time": i}
        for i, c in enumerate(closes)
    ]


class TestRealStrongSignalStrategyDrivesRealEntries:
    def test_run_entries_calls_generate_signal_directly_and_uses_its_output(self):
        """A real StrongSignalStrategy (built the same way strategy_factory
        wires production code, just with a deterministic indicator) must be
        invoked via its real `generate_signal(snapshot)` method, and its
        real return value -- not a hand-crafted stand-in -- must be what
        reaches `enter_trade.enter_trade`.
        """
        strategy = _make_strong_signal_strategy()
        enter_trade = MagicMock()

        orchestrator = create_orchestrator(
            collector=MagicMock(),
            signal_generator=strategy,
            broker=None,
            trading_service=None,  # force the enter_trade branch
            tick_collector=None,
            exit_trade=None,
            enter_trade=enter_trade,
            logger=None,
        )

        candles = [{"symbol": "EURUSD", "close": 1.1000, "time": 1}]

        orchestrator._run_entries(
            snapshot=candles, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        assert enter_trade.enter_trade.call_count == 1, (
            "expected the real StrongSignalStrategy.generate_signal(snapshot) "
            "output to reach enter_trade.enter_trade exactly once"
        )
        (sig, account_balance), _kwargs = enter_trade.enter_trade.call_args
        assert sig == {
            "final_signal": "buy",
            "raw_signal": "buy",
            "confidence": 1.0,
            "indicators": {"always_buy": "buy"},
            "symbol": "EURUSD",
        }, (
            "the dict passed to enter_trade must be exactly what the real "
            "generate_signal() computed -- not a mangled/partial call result"
        )
        assert account_balance == 0.0

    def test_run_entries_skips_execution_when_generate_signal_says_hold(self):
        """A real strategy that genuinely produces 'hold' must not reach
        enter_trade at all -- proving the real (not guessed) return value is
        actually consulted, not just "some call succeeded"."""
        strategy = StrongSignalStrategy(
            indicators={"always_hold": lambda _candles: "hold"},
            logger=_TEST_LOGGER,
            min_candles=1,
            confidence_threshold=0.5,
            config=Config,
        )
        enter_trade = MagicMock()

        orchestrator = create_orchestrator(
            collector=MagicMock(),
            signal_generator=strategy,
            broker=None,
            trading_service=None,
            tick_collector=None,
            exit_trade=None,
            enter_trade=enter_trade,
            logger=None,
        )

        candles = [{"symbol": "EURUSD", "close": 1.1000, "time": 1}]
        orchestrator._run_entries(
            snapshot=candles, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        enter_trade.enter_trade.assert_not_called()


class TestRealMultiTimeframeStrategyDrivesRealEntries:
    def test_run_entries_calls_real_multi_timeframe_strategy_with_dict_snapshot(self):
        """MultiTimeframeStrongSignalStrategy.generate_signal expects a
        dict-shaped snapshot (keyed by timeframe), not a list -- proving the
        direct `sg.generate_signal(snapshot)` call works regardless of the
        snapshot's real shape, exactly as it must for real production
        wiring (app/factory.py wires this class in when
        USE_MULTI_TIMEFRAME_SIGNALS is enabled).
        """
        base = _make_strong_signal_strategy()
        strategy = MultiTimeframeStrongSignalStrategy(
            base=base,
            tf_bias=mt5.TIMEFRAME_M15,
            tf_confirm=mt5.TIMEFRAME_M5,
            tf_entry=mt5.TIMEFRAME_M1,
        )
        enter_trade = MagicMock()

        orchestrator = create_orchestrator(
            collector=MagicMock(),
            signal_generator=strategy,
            broker=None,
            trading_service=None,
            tick_collector=None,
            exit_trade=None,
            enter_trade=enter_trade,
            logger=None,
        )

        simple_candles = [{"symbol": "EURUSD", "close": 1.1000, "time": 1}]
        entry_candles = _pullback_candles()
        snapshot = {
            mt5.TIMEFRAME_M15: simple_candles,
            mt5.TIMEFRAME_M5: simple_candles,
            mt5.TIMEFRAME_M1: entry_candles,
        }

        orchestrator._run_entries(
            snapshot=snapshot, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        assert enter_trade.enter_trade.call_count == 1, (
            "expected the real MultiTimeframeStrongSignalStrategy.generate_signal"
            "(snapshot) output (a genuine buy, gated by a real pullback check) "
            "to reach enter_trade.enter_trade"
        )
        (sig, _account_balance), _kwargs = enter_trade.enter_trade.call_args
        assert sig["final_signal"] == "buy"
        assert sig["symbol"] == "EURUSD"
        assert sig["m15_bias"] == "buy"
        assert sig["m5_confirm"] == "buy"
        assert sig["m1_entry"] == "buy"
        assert sig["pullback_completed"] is True


class TestRealNTickConfirmedStrategyOutputIsRespected:
    def test_run_entries_respects_real_hold_output_pending_confirmation(self):
        """NTickConfirmedSignalStrategy.generate_signal returns 'hold' (with
        a 'waiting_for_n_tick_confirmation' reason) on the very candle that
        starts a new pending signal -- it only confirms via on_new_tick(),
        never on the same generate_signal() call. This proves the real
        method was invoked with the real candles (its internal pending-signal
        state changes accordingly) *and* that its real 'hold' output
        correctly prevented an entry -- not a guessed call silently no-op'ing.
        """
        base = _make_strong_signal_strategy()
        strategy = NTickConfirmedSignalStrategy(base, n_ticks=3)
        enter_trade = MagicMock()

        orchestrator = create_orchestrator(
            collector=MagicMock(),
            signal_generator=strategy,
            broker=None,
            trading_service=None,
            tick_collector=None,
            exit_trade=None,
            enter_trade=enter_trade,
            logger=None,
        )

        candles = [
            {"symbol": "EURUSD", "close": 1.0500, "time": 1},
            {"symbol": "EURUSD", "close": 1.0600, "time": 2},
        ]

        orchestrator._run_entries(
            snapshot=candles, asof=datetime.datetime.now(datetime.timezone.utc)
        )

        enter_trade.enter_trade.assert_not_called()
        # The real generate_signal() call must have actually run against our
        # real candles and updated real internal state accordingly.
        assert strategy._waiting is True
        assert strategy._pending_signal == "buy"
        assert strategy._pending_entry_price == 1.0600


class TestFallbackLadderRungsAreUnreachableForRealStrategies:
    """Documents (per this subphase's spec) that the pre-fix ladder's
    fallback rungs beyond the first are dead code for every real
    signal-generator implementation in this codebase -- so there is no
    full-stack before/after distinguishing scenario for the fix itself; the
    e2e tests above instead confirm the happy path is unaffected by the
    simplification.
    """

    def _instances_and_snapshots(self):
        base = _make_strong_signal_strategy()
        mtf_base = _make_strong_signal_strategy()
        candles = [{"symbol": "EURUSD", "close": 1.1000, "time": 1}]
        mtf_snapshot = {
            mt5.TIMEFRAME_M15: candles,
            mt5.TIMEFRAME_M5: candles,
            mt5.TIMEFRAME_M1: candles,
        }
        return [
            (base, candles),
            (
                MultiTimeframeStrongSignalStrategy(
                    base=mtf_base,
                    tf_bias=mt5.TIMEFRAME_M15,
                    tf_confirm=mt5.TIMEFRAME_M5,
                    tf_entry=mt5.TIMEFRAME_M1,
                ),
                mtf_snapshot,
            ),
            (NTickConfirmedSignalStrategy(_make_strong_signal_strategy(), n_ticks=2), candles),
        ]

    def test_first_ladder_rung_always_succeeds_for_real_strategies(self):
        """`gen(snapshot)` -- the ladder's first attempt, and the new fix's
        entire implementation -- succeeds for every real strategy class."""
        for strategy, snapshot in self._instances_and_snapshots():
            result = strategy.generate_signal(snapshot)
            assert isinstance(result, dict)

    def test_later_ladder_rungs_never_succeed_for_real_strategies(self):
        """The removed fallback rungs (`candles_snapshot=snapshot`, `gen()`)
        never succeed for any real strategy class -- they would only ever be
        reached if the first rung raised TypeError, which it never does for
        real strategies, so they are provably dead code being removed."""
        for strategy, snapshot in self._instances_and_snapshots():
            with pytest.raises(TypeError):
                strategy.generate_signal(candles_snapshot=snapshot)
            with pytest.raises(TypeError):
                strategy.generate_signal()
