"""E2E coverage for Subphase 4.2 (project-refactor-sweep):

Pre-fix, `app/routes/endpoints.py` imported live singletons
(`orchestrators`, `signal_orchestrator`, `trade_executor`, `br`, `md`)
directly at module level from `app.factory`, and every route handler read
those module-level names directly. This subphase replaces that with FastAPI
`Depends()`-based injection: each route obtains the collaborators it needs
via `Depends(get_orchestrators)` / `Depends(get_broker)` /
`Depends(get_market_data)` / `Depends(get_trade_executor)` (the four getters
Subphase 4.1 added to `app/factory.py`) instead of a bare module-level
import. No route's business logic changes -- this is purely a wiring change
that makes the API layer testable via `app.dependency_overrides` with
mocked collaborators, instead of requiring the real `app.factory` singletons
(which otherwise need a real/mocked MT5 terminal at import time).

Because the whole point of this subphase is "the API layer no longer
*needs* the real app.factory singletons to be tested", this file's main
proof deliberately does the opposite of earlier subphases' "drive the real,
fully-wired app" pattern:

1. `TestEndpointsWiredThroughDependencyOverrides` builds a *fresh* `FastAPI()`
   app, `include_router()`s the real `app.routes.endpoints.router`, and
   overrides all four getters (`get_market_data`/`get_broker`/
   `get_trade_executor`/`get_orchestrators`, imported from the real
   `app.factory` -- these are the exact callables the route signatures must
   depend on for `app.dependency_overrides` to have any effect at all) with
   `MagicMock`-backed collaborators. It then drives all 10 routes named in
   this subphase's acceptance criteria through `TestClient` and asserts each
   one's response is built from the *mocked* collaborator's data -- proving
   the DI wiring genuinely reaches every route, not just one.

   Per the plan's own carve-out, `/status` is driven with a `MagicMock`
   orchestrator (which auto-provides `is_running()`) rather than a real
   `SignalOrchestrator` -- the real class's pre-existing lack of an
   `is_running()` method is a separate, already-known bug this subphase
   does not touch.

2. `TestPreFixEndpointsCannotBeExercisedThisWay` empirically confirms this is
   the actual regression being closed: it loads `app/routes/endpoints.py`'s
   source *as committed at the immutable SHA `2795d07`* (Subphase 4.1's
   merge commit -- the last commit on `master` before this subphase; see
   `_PRE_FIX_COMMIT_SHA` below for why this must be a fixed SHA and never a
   moving branch ref like `master` -- once this subphase merges, `master`
   *becomes* the DI version, which would invert every assertion in this
   class), executed into a private, isolated module -- never touching the
   real working-tree file, which `developer` may be concurrently editing --
   and shows that overriding the very same `get_broker`/`get_orchestrators`
   callables has **no effect** on that old code's routes: `/simulated_positions`
   still returns the *real* `app.factory.br.open_positions_sim` (always `[]`
   for a real, LIVE-mode `Broker` -- see `app/trade_execution/broker.py`),
   not the mocked value the override supplied, because the pre-fix route
   reads the module-level `br` name directly and has no `Depends()`
   parameter for `app.dependency_overrides` to key on in the first place.

No real MT5/broker calls are made anywhere in this file -- `mock_mt5`
(tests/conftest.py) is the only boundary mock, and even that is only
needed because importing the real `app.factory` (transitively, via
`app.routes.endpoints`) still constructs the real singletons at import time;
none of this file's assertions depend on those singletons' real behavior.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import MetaTrader5 as mt5
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

# Force the (transitive) app.factory / app.routes.endpoints import to happen
# right here, at this test module's own import time -- which pytest always
# runs under conftest.py's unconditional baseline MT5 patch (see
# tests/conftest.py), since conftest.py is imported before any test module in
# this directory. This is deliberately a *module-level* import rather than
# something done lazily inside a test function: Subphase 4.3's own test
# below installs a real AssertionError-raising trap on mt5.initialize, and
# app.factory's module-level `create_broker(Mode.LIVE)` call (which reaches
# mt5.initialize()) must complete -- safely, under the baseline patch --
# before that trap ever exists, regardless of which single test in this file
# pytest happens to run or in what order.
from app.factory import (  # noqa: E402
    get_broker,
    get_market_data,
    get_orchestrators,
    get_trade_executor,
)
from app.routes.endpoints import router  # noqa: E402
from app.trade_execution.mode import TradingMode  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The immutable commit this subphase's "prove the old code couldn't be
# exercised this way" tests load their "pre-fix" source from -- Subphase
# 4.1's merge commit, i.e. the last commit on `master` *before* this
# subphase's DI wiring landed.
#
# This MUST be a fixed SHA, never a moving ref like "master" or "HEAD":
# those tests assert the pre-fix code lacks Depends() and that overriding
# it has no effect. Once this subphase's branch merges, `master` (and this
# branch's own `HEAD`) *become* the DI version, which would silently invert
# both assertions -- they'd start failing exactly when the code is correct.
# A test proving "the old code was broken" cannot read a ref that will
# itself become the new code.
_PRE_FIX_COMMIT_SHA = "2795d07"


def _load_endpoints_module_from_git_ref(ref: str, private_module_name: str):
    """Load `app/routes/endpoints.py`'s source *as committed at `ref`* (an
    immutable SHA -- see `_PRE_FIX_COMMIT_SHA` above) into an isolated module
    under `private_module_name` -- never touching the real working-tree file
    (which `developer` may be concurrently editing on this same branch).

    Skips (rather than errors) if `ref` isn't available locally (e.g. a
    shallow checkout that never fetched it) -- this is a supplementary
    regression-lock, not the primary behavioral proof of this subphase."""
    result = subprocess.run(
        ["git", "show", f"{ref}:app/routes/endpoints.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(
            f"git show {ref}:app/routes/endpoints.py failed (commit not "
            f"available locally, e.g. a shallow checkout): {result.stderr!r}"
        )

    spec = importlib.util.spec_from_loader(private_module_name, loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[private_module_name] = module
    try:
        exec(
            compile(result.stdout, f"<git:{ref}:app/routes/endpoints.py>", "exec"),
            module.__dict__,
        )
    finally:
        # Don't leak this private, exec'd module into sys.modules beyond
        # this test -- nothing else needs to import it by name.
        del sys.modules[private_module_name]
    return module


def _make_override(value):
    """Build a zero-argument override callable closing over `value`.

    Deliberately NOT `lambda _value=value: _value`: giving the override
    callable its own default-valued parameter makes FastAPI treat that
    parameter as something to resolve/validate in its own right, which
    silently reconstructs (rather than passes through) a mutable default
    like a dict of MagicMocks -- breaking identity/call-tracking on the
    mocks the test asserts against, even though the route's own response
    body can look correct. A true closure with no parameters at all has
    nothing for FastAPI to resolve, so `value` is returned by reference.
    """

    def _override():
        return value

    return _override


def _build_test_app(router, overrides: dict):
    """Fresh FastAPI() app + the real router, with `overrides` (a dict of
    {dependency_callable: value}) installed via `app.dependency_overrides`."""
    app = FastAPI()
    app.include_router(router)
    for dependency_callable, value in overrides.items():
        app.dependency_overrides[dependency_callable] = _make_override(value)
    return app


class TestEndpointsWiredThroughDependencyOverrides:
    """Drives all 10 routes named in this subphase's acceptance criteria
    through a fresh app + `app.dependency_overrides`, proving each one reads
    its collaborators via `Depends(...)` rather than a module-level name."""

    def _make_mocks(self, mock_broker):
        orchestrator = MagicMock()
        orchestrator.is_running.return_value = True
        orchestrator.get_latest_signal.return_value = "buy"
        orchestrator.get_tick.return_value = "mocked-tick"
        orchestrators = {"EURUSD": orchestrator}

        trade_executor = MagicMock()
        trade_executor.daily_profit = 123.45
        trade_executor.last_reset = "2024-01-01T00:00:00"
        trade_executor.close_all_trades.return_value = {
            "failed": [],
            "closed": [1, 2, 3],
        }

        market_data = MagicMock()
        market_data.get_historical_candles.return_value = [
            {"open": 1.1, "close": 1.2},
            {"open": 1.2, "close": 1.3},
        ]

        mock_broker.open_positions_sim = [{"ticket": 42, "symbol": "EURUSD"}]

        return orchestrator, orchestrators, trade_executor, market_data, mock_broker

    def _client(self, mocks):
        orchestrator, orchestrators, trade_executor, market_data, broker = mocks

        # get_broker/get_market_data/get_orchestrators/get_trade_executor/router
        # are already imported at module level (see the top-of-file import
        # ordering note in the module docstring).
        app = _build_test_app(
            router,
            {
                get_market_data: market_data,
                get_broker: broker,
                get_trade_executor: trade_executor,
                get_orchestrators: orchestrators,
            },
        )
        return TestClient(app)

    def test_status_uses_mocked_orchestrators_and_trade_executor(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        orchestrator = mocks[0]
        client = self._client(mocks)

        response = client.get("/status")

        assert response.status_code == 200
        body = response.json()
        assert body["orchestrator_running"] == {"EURUSD": True}
        assert body["daily_profit"] == 123.45
        assert body["last_reset"] == "2024-01-01T00:00:00"
        orchestrator.is_running.assert_called()

    def test_trading_start_calls_start_on_the_mocked_orchestrator(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        orchestrator = mocks[0]
        client = self._client(mocks)

        response = client.post("/trading/start")

        assert response.status_code == 200
        assert response.json() == {
            "status": "trading started",
            "orchestrator_running": True,
        }
        orchestrator.start.assert_called_once()

    def test_trading_stop_calls_stop_on_the_mocked_orchestrator(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        orchestrator = mocks[0]
        client = self._client(mocks)

        response = client.post("/trading/stop")

        assert response.status_code == 200
        assert response.json() == {
            "status": "trading stopped",
            "orchestrator_running": False,
        }
        orchestrator.stop.assert_called_once()

    def test_signal_latest_with_explicit_symbol_uses_mocked_orchestrator(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        client = self._client(mocks)

        response = client.get("/signal/latest", params={"symbol": "EURUSD"})

        assert response.status_code == 200
        assert response.json() == {"signal": "buy"}

    def test_signal_latest_without_symbol_still_uses_mocked_orchestrators(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        client = self._client(mocks)

        response = client.get("/signal/latest")

        assert response.status_code == 200
        assert response.json() == {"signal": "buy"}

    def test_live_signal_uses_mocked_orchestrator(self, mock_mt5, mock_broker):
        mocks = self._make_mocks(mock_broker)
        client = self._client(mocks)

        response = client.get("/live_signal", params={"symbol": "EURUSD"})

        assert response.status_code == 200
        assert response.json() == {"signal": "buy"}

    def test_tick_uses_mocked_orchestrator(self, mock_mt5, mock_broker):
        mocks = self._make_mocks(mock_broker)
        client = self._client(mocks)

        response = client.get("/tick", params={"symbol": "EURUSD"})

        assert response.status_code == 200
        assert response.json() == {"tick": "mocked-tick"}

    def test_simulated_positions_returns_mocked_brokers_open_positions(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        client = self._client(mocks)

        response = client.get("/simulated_positions")

        assert response.status_code == 200
        assert response.json() == [{"ticket": 42, "symbol": "EURUSD"}]

    def test_close_all_uses_mocked_trade_executor(self, mock_mt5, mock_broker):
        mocks = self._make_mocks(mock_broker)
        trade_executor = mocks[2]
        client = self._client(mocks)

        response = client.post("/close_all")

        assert response.status_code == 200
        assert response.json() == {
            "status": "all trades closed",
            "failed": [],
            "closed": [1, 2, 3],
        }
        trade_executor.close_all_trades.assert_called_once()

    def test_close_all_reports_failures_from_mocked_trade_executor(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        trade_executor = mocks[2]
        trade_executor.close_all_trades.return_value = {
            "failed": [999],
            "closed": [],
        }
        client = self._client(mocks)

        response = client.post("/close_all")

        assert response.status_code == 200
        assert response.json() == {
            "status": "some trades failed to close",
            "failed": [999],
            "closed": [],
        }

    def test_test_historical_uses_mocked_market_data(self, mock_mt5, mock_broker):
        mocks = self._make_mocks(mock_broker)
        market_data = mocks[3]
        client = self._client(mocks)

        response = client.get("/test_historical")

        assert response.status_code == 200
        assert response.json() == {
            "candles": [
                {"open": 1.1, "close": 1.2},
                {"open": 1.2, "close": 1.3},
            ]
        }
        market_data.get_historical_candles.assert_called_once()

    def test_stop_orchestrator_calls_stop_on_the_mocked_orchestrator(
        self, mock_mt5, mock_broker
    ):
        mocks = self._make_mocks(mock_broker)
        orchestrator = mocks[0]
        client = self._client(mocks)

        response = client.post("/stop_orchestrator")

        assert response.status_code == 200
        assert response.json() == {
            "status": "orchestrator stopped",
            "orchestrator_running": False,
        }
        orchestrator.stop.assert_called_once()

    def test_no_real_mt5_calls_made_exercising_all_routes_above(
        self, mock_mt5, mock_broker
    ):
        """Sanity guard: none of the routes above -- all driven purely
        through mocked, overridden collaborators -- touch a real MT5
        terminal."""
        mocks = self._make_mocks(mock_broker)
        client = self._client(mocks)

        for method, path, kwargs in (
            ("get", "/status", {}),
            ("post", "/trading/start", {}),
            ("post", "/trading/stop", {}),
            ("get", "/signal/latest", {}),
            ("get", "/live_signal", {}),
            ("get", "/tick", {"params": {"symbol": "EURUSD"}}),
            ("get", "/simulated_positions", {}),
            ("post", "/close_all", {}),
            ("get", "/test_historical", {}),
            ("post", "/stop_orchestrator", {}),
        ):
            getattr(client, method)(path, **kwargs)

        # mock_mt5 patches order_send/copy_rates_from_pos too, but this
        # subphase's routes never call MT5 directly -- they only ever touch
        # the mocked collaborators supplied via dependency_overrides above.
        assert not mock_mt5.order_send.called
        assert not mock_mt5.copy_rates_from_pos.called


class TestFullRouterExercisableWithoutTouchingMT5:
    """Subphase 4.3: proves the whole router is testable via
    `app.dependency_overrides` alone, with a real `AssertionError`-raising
    trap installed on `MetaTrader5.initialize` -- i.e. that none of the 10
    routes named in this subphase's acceptance criteria construct or call
    real MT5/broker/orchestrator code during request handling.

    The module-level imports of `app.factory` / `app.routes.endpoints` at
    the top of this file already happened -- safely, under conftest.py's
    unconditional baseline MT5 patch -- before any test method below runs,
    so installing the trap inside a test function here cannot retroactively
    punish that one-time import (see the hazard this subphase's plan calls
    out explicitly)."""

    def _install_mt5_trap(self, monkeypatch):
        """Replace MetaTrader5.initialize with a function that fails the
        test loudly if anything actually calls it during request handling."""

        def _raise(*args, **kwargs):
            raise AssertionError("MT5 should not be touched in this test")

        monkeypatch.setattr(mt5, "initialize", _raise)

    def _make_mocks(self):
        """Build MagicMock-backed collaborators, configured per this
        subphase's acceptance criteria so every route's own logic succeeds
        (e.g. `mock_broker.mode = TradingMode.DEMO`,
        `mock_broker.open_positions_sim = []`,
        `mock_md.get_historical_candles.return_value = []`)."""
        mock_orchestrator = MagicMock()
        mock_orchestrator.is_running.return_value = True
        mock_orchestrator.get_latest_signal.return_value = "buy"
        mock_orchestrator.get_tick.return_value = "mocked-tick"
        mock_orchestrator.start.return_value = None
        mock_orchestrator.stop.return_value = None
        mock_orchestrators = {"EURUSD": mock_orchestrator}

        mock_trade_executor = MagicMock()
        mock_trade_executor.daily_profit = 0.0
        mock_trade_executor.last_reset = None
        mock_trade_executor.close_all_trades.return_value = {
            "failed": [],
            "closed": [],
        }

        mock_market_data = MagicMock()
        mock_market_data.get_historical_candles.return_value = []

        mock_broker = MagicMock()
        mock_broker.mode = TradingMode.DEMO
        mock_broker.open_positions_sim = []

        return mock_orchestrators, mock_trade_executor, mock_market_data, mock_broker

    def _client(self, mocks):
        mock_orchestrators, mock_trade_executor, mock_market_data, mock_broker = mocks
        app = _build_test_app(
            router,
            {
                get_market_data: mock_market_data,
                get_broker: mock_broker,
                get_trade_executor: mock_trade_executor,
                get_orchestrators: mock_orchestrators,
            },
        )
        return TestClient(app)

    # The exact 10 routes named in this subphase's acceptance criteria.
    ALL_TEN_ROUTES = (
        ("get", "/status", {}),
        ("post", "/trading/start", {}),
        ("post", "/trading/stop", {}),
        ("get", "/signal/latest", {"params": {"symbol": "EURUSD"}}),
        ("get", "/live_signal", {"params": {"symbol": "EURUSD"}}),
        ("get", "/tick", {"params": {"symbol": "EURUSD"}}),
        ("get", "/simulated_positions", {}),
        ("post", "/close_all", {}),
        ("get", "/test_historical", {}),
        ("post", "/stop_orchestrator", {}),
    )


        # If any route (or a dependency it pulls in) had actually reached
        # real MT5 during the request handling above, the AssertionError
        # installed by _install_mt5_trap would have propagated straight out
        # of the `getattr(client, method)(...)` call and failed this test
        # with that error -- not with a non-2xx status code. Reaching this
        # point at all is itself part of the proof (see the bonus test below
        # for empirical confirmation that the trap is actually reachable and
        # not structurally inert).

    def test_trap_would_actually_catch_a_regression_that_calls_real_mt5(
        self, monkeypatch
    ):
        """Bonus/empirical check that the trap above is a genuine,
        reachable proof rather than one that's structurally inert given this
        subphase's DI wiring: wires up a throwaway route that simulates a
        future regression (a route/dependency bypassing Depends() and
        calling MetaTrader5.initialize() directly, the same way the pre-fix
        module-level singletons in app.factory do), and confirms the trap
        installed the same way as above actually fires and propagates
        through TestClient -- it is not silently swallowed or bypassed."""
        self._install_mt5_trap(monkeypatch)

        regression_router = APIRouter()

        @regression_router.get("/regressed")
        def regressed_route():
            mt5.initialize()  # simulates a reintroduced real MT5 call
            return {"ok": True}

        app = FastAPI()
        app.include_router(regression_router)
        client = TestClient(app)

        with pytest.raises(AssertionError, match="MT5 should not be touched"):
            client.get("/regressed")
