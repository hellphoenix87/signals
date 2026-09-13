"""E2E coverage for Subphase 5.3 (project-refactor-sweep):

Pre-fix, `app/exit_strategies/exit_trade.py::ExitTrade.on_candle_close` already
called `self._profit_manager.check_exit_on_candle_close(pos, close_price,
state)`, but `ProfitExitManager` (`app/exit_strategies/managers/profit.py`)
only ever defined `check_exit_on_tick` -- that call would have raised
`AttributeError` the moment `Config.EXIT_PROFIT_EXITS_ON_CANDLE_CLOSE` (or an
explicit `ExitTradeConfig(profit_exits_on_candle_close=True)`) was ever turned
on. This subphase adds the real method, mirroring `check_exit_on_tick`'s
break-even-arming / best-profit-tracking / $0.04 trailing-breach logic but
driven off a candle's `close_price` instead of a live tick's bid/ask, *and*
wires up the previously-unused `htf_allows_profit_exit` callable so the whole
method is gated on higher-timeframe bias: HTF support for the position's
current side suppresses the profit exit; HTF opposition lets it through.

This is money-moving exit-strategy logic (this subphase's triage was bumped
to elevated), so this file is held to Phase 2/3's e2e authoring rigor, not
Phase 5's otherwise-pure test-backfill bar. It drives a real `ExitTrade`
wired with the real `ProfitExitManager`/`LossExitManager` (via a real
`Broker` in `TradingMode.DEMO`, same convention as
`tests/e2e/test_exit_trade_cooldown_gate.py`), and separately proves this is
a genuine regression-closing test -- not just a logic nuance -- by loading
`app/exit_strategies/managers/profit.py`'s source *as committed at the
immutable branch-base SHA `7213572`* (this branch's own tip before this
subphase's implementation landed -- see `_PRE_FIX_COMMIT_SHA` below) and
showing the exact call path `ExitTrade.on_candle_close` already used would
raise `AttributeError` against that pre-fix source.

Only MT5 itself is mocked at the boundary (`mock_mt5` fixture plus a local
`mt5.symbol_info` stub, following `tests/e2e/test_orchestrator_enter_trade_dispatch.py`'s
precedent -- `mock_mt5` alone doesn't cover `symbol_info`, which
`ProfitExitManager`'s pip-conversion helpers reach into via
`Broker.get_pip_size`); nothing else in this file is faked.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.exit_strategies.exit_shared import pos_ticket
from app.exit_strategies.exit_trade import ExitTrade, ExitTradeConfig
from app.exit_strategies.managers.profit import ProfitExitManager
from app.trade_execution.broker import create_broker
from app.trade_execution.mode import TradingMode

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The immutable commit this subphase's "prove the method didn't exist
# pre-fix" tests load their "before" source from -- this branch's own tip
# (the plan-only amend commit that bumped this subphase's triage) *before*
# this subphase's real implementation landed.
#
# This MUST stay a fixed SHA, never a moving ref like "master"/"HEAD": once
# this subphase's branch merges, both become the fixed version, which would
# silently invert the assertions below (they prove the *old* code lacked the
# method and would raise -- a ref that becomes the new code can't serve that
# purpose forever).
_PRE_FIX_COMMIT_SHA = "7213572"


class _FakeSymbolInfo:
    """Minimal stand-in for mt5.symbol_info()'s return value.

    Only the fields Broker's pip/point helpers actually read are populated.
    """

    def __init__(self):
        self.point = 0.00001
        self.digits = 5
        self.volume_min = 0.01
        self.volume_max = 100.0
        self.volume_step = 0.01
        self.trade_stops_level = 0
        self.trade_contract_size = 100000.0


def _stub_symbol_info(mock_mt5, monkeypatch):
    """mock_mt5 (tests/conftest.py) doesn't patch mt5.symbol_info --
    ProfitExitManager's pip-conversion helpers reach it via
    Broker.get_pip_size, so real end-to-end execution needs it stubbed too.
    Mirrors tests/e2e/test_orchestrator_enter_trade_dispatch.py's helper."""
    import MetaTrader5 as mt5

    monkeypatch.setattr(mt5, "symbol_info", lambda symbol: _FakeSymbolInfo())


def _stub_tick_price(mock_mt5, make_tick, bid=1.1000, ask=1.1002):
    """Broker._simulate_trade calls mt5.symbol_info_tick(symbol) to price a
    demo trade; without this it silently falls back to price=1.0. Mirrors
    tests/e2e/test_exit_trade_cooldown_gate.py's helper of the same name.
    """
    mock_mt5.symbol_info_tick.return_value = make_tick(bid=bid, ask=ask)


def _disable_exit_cooldown(exit_trade: ExitTrade) -> None:
    """Disable this ExitTrade instance's per-ticket exit cooldown (already
    proven correct in Subphase 5.2's own e2e coverage) so this file's tests
    -- which deliberately call on_candle_close several times in a row for
    the same ticket, with no sleep in between, to walk one position through
    arming / breach / HTF-gate-flip -- aren't incidentally suppressed by it.
    This file is isolating the profit-exit/HTF-gating logic under test, not
    the cooldown gate.

    Deliberately sets the already-constructed instance's `_exit_cooldown`
    attribute directly rather than monkeypatching
    `Config.EXIT_COOLDOWN_SECONDS` beforehand: `ExitTrade.__init__` reads it
    as `float(getattr(Config, "EXIT_COOLDOWN_SECONDS", 2.0) or 2.0)` -- that
    trailing `or 2.0` means a monkeypatched `0.0` (falsy) would silently
    fall back to the 2.0 default instead of actually disabling the
    cooldown, defeating the whole point. Setting the instance attribute
    directly, after construction, sidesteps that `or`-with-falsy-zero
    re-evaluation entirely.
    """
    exit_trade._exit_cooldown = 0.0


def _load_profit_module_from_git_ref(ref: str, private_module_name: str):
    """Load `app/exit_strategies/managers/profit.py`'s source *as committed
    at `ref`* (an immutable SHA -- see `_PRE_FIX_COMMIT_SHA` above) into an
    isolated module under `private_module_name` -- never touching the real
    working-tree file (which `developer` may be concurrently editing on this
    same branch). Mirrors
    tests/e2e/test_endpoints_dependency_injection.py's loader of the same
    shape."""
    result = subprocess.run(
        ["git", "show", f"{ref}:app/exit_strategies/managers/profit.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(
            f"git show {ref}:app/exit_strategies/managers/profit.py failed "
            f"(commit not available locally, e.g. a shallow checkout): "
            f"{result.stderr!r}"
        )

    spec = importlib.util.spec_from_loader(private_module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[private_module_name] = module
    try:
        exec(
            compile(
                result.stdout,
                f"<git:{ref}:app/exit_strategies/managers/profit.py>",
                "exec",
            ),
            module.__dict__,
        )
    finally:
        # Don't leak this private, exec'd module into sys.modules beyond
        # this test -- nothing else needs to import it by name.
        del sys.modules[private_module_name]
    return module


class TestOnCandleCloseHtfGating:
    """The plan's exact acceptance-criteria scenario: a real break-even-armed
    buy position with a genuine trailing-breach condition, gated on and then
    off as real HTF bias (via `update_bias`) flips from supportive to
    opposing."""

    def test_htf_supportive_bias_gates_off_profit_exit_then_opposing_bias_allows_it(
        self, mock_mt5, make_tick, monkeypatch
    ):
        _stub_tick_price(mock_mt5, make_tick)
        _stub_symbol_info(mock_mt5, monkeypatch)

        broker = create_broker(TradingMode.DEMO)
        broker.place_buy("EURUSD", 0.05, sl=1.0950, tp=1.1100)
        position = broker.get_open_positions()[0]
        ticket = pos_ticket(position)
        assert ticket is not None

        config = ExitTradeConfig(
            htf_filter_enabled=True,
            profit_exits_on_candle_close=True,
            profit_exits_on_tick=False,
        )
        exit_trade = ExitTrade(broker=broker, risk_manager=MagicMock(), config=config)
        _disable_exit_cooldown(exit_trade)

        # Step 1: break-even-arm the position (profit >= 0) and establish a
        # best-profit baseline of $0.10. No HTF bias has been recorded yet
        # for this symbol, so the gate defaults open -- this priming call
        # must not itself produce an exit (best_profit is only just being
        # initialized to the current profit, so there's no breach yet).
        position["profit"] = 0.10
        priming_actions = exit_trade.on_candle_close(
            symbol="EURUSD", close_price=1.1005
        )
        assert priming_actions == [], (
            "expected the arming/baseline-establishing call to produce no "
            f"exit action yet; got {priming_actions!r}"
        )

        # Step 2: profit drops from the $0.10 best seen to $0.04 -- a $0.06
        # breach, comfortably past ProfitExitManager's $0.04 trailing-breach
        # threshold. Absent HTF gating, this alone would trigger an exit.
        position["profit"] = 0.04

        # Step 3: HTF m15 bias is "buy" -- supportive of this existing buy
        # position -- so the otherwise-genuine profit-taking exit must be
        # suppressed entirely.
        exit_trade.update_bias("EURUSD", m15="buy")
        gated_actions = exit_trade.on_candle_close(
            symbol="EURUSD", close_price=1.1005
        )
        assert gated_actions == [], (
            "expected HTF bias supportive of the open buy position "
            f"(m15='buy') to gate off the profit-taking exit; got "
            f"{gated_actions!r}"
        )

        # Step 4: HTF m15 bias flips to "sell" -- opposing the buy position
        # -- so the same still-breached state must now be let through.
        exit_trade.update_bias("EURUSD", m15="sell")
        allowed_actions = exit_trade.on_candle_close(
            symbol="EURUSD", close_price=1.1005
        )
        assert len(allowed_actions) == 1, (
            "expected HTF bias opposing the open buy position (m15='sell') "
            f"to let the profit-taking exit through; got {allowed_actions!r}"
        )
        action = allowed_actions[0]
        assert action.ticket == ticket
        assert action.symbol == "EURUSD"
        assert action.side == "sell"  # closing side for an open buy position
        assert "breach" in action.reason.lower(), (
            f"expected a trailing-breach-flavored exit reason; got {action.reason!r}"
        )

    def test_no_real_mt5_calls_made(self, mock_mt5, make_tick, monkeypatch):
        """Sanity guard: exercising the real ExitTrade/ProfitExitManager
        candle-close path above never touches a real, unmocked MT5 call --
        order_send/copy_rates_from_pos (patched by mock_mt5) are never hit
        by this read-only exit-checking path, and symbol_info is stubbed
        locally (see _stub_symbol_info)."""
        _stub_tick_price(mock_mt5, make_tick)
        _stub_symbol_info(mock_mt5, monkeypatch)

        broker = create_broker(TradingMode.DEMO)
        broker.place_buy("EURUSD", 0.05, sl=1.0950, tp=1.1100)
        position = broker.get_open_positions()[0]
        position["profit"] = 0.10

        config = ExitTradeConfig(
            htf_filter_enabled=True,
            profit_exits_on_candle_close=True,
            profit_exits_on_tick=False,
        )
        exit_trade = ExitTrade(broker=broker, risk_manager=MagicMock(), config=config)
        _disable_exit_cooldown(exit_trade)
        exit_trade.on_candle_close(symbol="EURUSD", close_price=1.1005)

        assert not mock_mt5.order_send.called
        assert not mock_mt5.copy_rates_from_pos.called


class TestOnCandleCloseWithoutHtfFiltering:
    """Beyond the plan's literal minimum: proves the *other* half of this
    subphase's behavior explicitly -- with HTF filtering disabled outright,
    check_exit_on_candle_close's profit-taking logic still fires correctly
    off a candle's close_price, independent of any recorded HTF bias. Given
    this is exit/money logic, this is cheap, valuable coverage the plan's
    acceptance criteria alone didn't ask for."""

    def test_profit_exit_fires_from_close_price_independent_of_supportive_bias(
        self, mock_mt5, make_tick, monkeypatch
    ):
        _stub_tick_price(mock_mt5, make_tick)
        _stub_symbol_info(mock_mt5, monkeypatch)

        broker = create_broker(TradingMode.DEMO)
        broker.place_buy("GBPUSD", 0.10, sl=1.2400, tp=1.2700)
        position = broker.get_open_positions()[0]
        ticket = pos_ticket(position)

        config = ExitTradeConfig(
            htf_filter_enabled=False,
            profit_exits_on_candle_close=True,
            profit_exits_on_tick=False,
        )
        exit_trade = ExitTrade(broker=broker, risk_manager=MagicMock(), config=config)
        _disable_exit_cooldown(exit_trade)

        # Record an HTF bias that *would* be supportive of this buy position
        # if htf_filter_enabled were True -- it must have zero effect here,
        # since gating is disabled outright.
        exit_trade.update_bias("GBPUSD", m15="buy")

        position["profit"] = 0.10
        priming_actions = exit_trade.on_candle_close(
            symbol="GBPUSD", close_price=1.2550
        )
        assert priming_actions == []

        position["profit"] = 0.04
        actions = exit_trade.on_candle_close(symbol="GBPUSD", close_price=1.2550)
        assert len(actions) == 1, (
            "expected the real candle-close trailing-breach profit exit to "
            "fire purely from close_price/position profit when "
            f"htf_filter_enabled=False, regardless of recorded HTF bias; "
            f"got {actions!r}"
        )
        action = actions[0]
        assert action.ticket == ticket
        assert action.symbol == "GBPUSD"
        assert action.side == "sell"
        assert "breach" in action.reason.lower()


class TestPreFixProfitExitManagerLacksCandleCloseMethod:
    """Empirically confirms the regression this subphase closes: at the
    immutable pre-fix SHA (_PRE_FIX_COMMIT_SHA), ProfitExitManager had no
    check_exit_on_candle_close method at all -- so the exact call path
    ExitTrade.on_candle_close already used (`self._profit_manager
    .check_exit_on_candle_close(...)`) would have raised AttributeError the
    moment profit_exits_on_candle_close was ever turned on."""

    def test_pre_fix_source_has_no_check_exit_on_candle_close_method(self):
        pre_fix_module = _load_profit_module_from_git_ref(
            _PRE_FIX_COMMIT_SHA, "_pre_fix_profit_5_3_structural"
        )
        assert not hasattr(
            pre_fix_module.ProfitExitManager, "check_exit_on_candle_close"
        ), (
            "expected the pre-fix ProfitExitManager (as committed at "
            f"{_PRE_FIX_COMMIT_SHA}, before this subphase's real "
            "implementation) to have no check_exit_on_candle_close method "
            "at all"
        )
        # Sanity: the CURRENT, real class now does define it -- confirms
        # this subphase actually closed the gap, rather than the pre-fix
        # source simply happening to match by coincidence.
        assert hasattr(ProfitExitManager, "check_exit_on_candle_close")

    def test_real_exit_trade_on_candle_close_raises_attributeerror_with_pre_fix_manager(
        self, mock_mt5, make_tick, monkeypatch
    ):
        """Swaps the real, wired ExitTrade's profit manager for one built
        from the pre-fix source (identical collaborators/wiring otherwise)
        and proves the exact call path `ExitTrade.on_candle_close` already
        used would have raised AttributeError against that pre-fix code --
        the dormant crash this subphase's real implementation closes, not
        just a logic nuance."""
        _stub_tick_price(mock_mt5, make_tick)
        _stub_symbol_info(mock_mt5, monkeypatch)

        broker = create_broker(TradingMode.DEMO)
        broker.place_buy("EURUSD", 0.05, sl=1.0950, tp=1.1100)
        position = broker.get_open_positions()[0]
        position["profit"] = 0.10

        config = ExitTradeConfig(
            htf_filter_enabled=False,
            profit_exits_on_candle_close=True,
            profit_exits_on_tick=False,
        )
        exit_trade = ExitTrade(broker=broker, risk_manager=MagicMock(), config=config)
        # Only one on_candle_close call is made below, so the cooldown gate
        # doesn't need disabling here -- unlike the multi-call scenarios
        # above.

        pre_fix_module = _load_profit_module_from_git_ref(
            _PRE_FIX_COMMIT_SHA, "_pre_fix_profit_5_3_wired"
        )
        # Same collaborator wiring ExitTrade.__init__ uses for the real
        # ProfitExitManager (see app/exit_strategies/exit_trade.py) -- only
        # the class itself is swapped for the pre-fix one.
        exit_trade._profit_manager = pre_fix_module.ProfitExitManager(
            config=exit_trade._config,
            broker=exit_trade._broker,
            get_min_profit_pips=exit_trade._get_min_profit_pips,
            dynamic_buffer=exit_trade._dynamic_buffer,
            htf_allows_profit_exit=exit_trade._htf_allows_profit_exit,
            pips_to_price=exit_trade._pips_to_price,
            is_favorable_vs_anchor=exit_trade._is_favorable_vs_anchor,
            exit_action=exit_trade._exit_action,
        )

        with pytest.raises(AttributeError):
            exit_trade.on_candle_close(symbol="EURUSD", close_price=1.1005)
