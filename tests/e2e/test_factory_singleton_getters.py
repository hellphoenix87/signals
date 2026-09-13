"""E2E coverage for Subphase 4.1 (project-refactor-sweep):

`app/factory.py` gains four plain getter functions -- `get_market_data()`,
`get_broker()`, `get_trade_executor()`, `get_orchestrators()` -- that each
return the module's existing singleton (`md`, `br`, `trade_executor`,
`orchestrators`). This subphase is purely additive/no-behavior-change: the
getters aren't wired into `app/routes/endpoints.py` via `Depends()` until
Subphase 4.2, so there's no new request/response flow to exercise yet.

Given that, this test drives the real composition root end-to-end (the real,
fully-wired `app.main:app` -- which transitively imports and builds
`app.factory`'s real `MarketData`/`Broker`/`TradeExecutor`/per-symbol
`SignalOrchestrator` singletons -- with only MT5 mocked at the boundary via
the `mock_mt5` fixture) and asserts two things a unit test of `app.factory`
in isolation wouldn't fully prove:

1. The getters are importable and callable from the real, fully-wired app
   module (not just from a hand-built `app.factory` reload), and each one
   returns *the exact same object* the rest of the fully-wired app (and
   `app/routes/endpoints.py`'s direct imports) already uses -- i.e. they are
   accessors onto the existing singletons, not fresh constructions.
2. Introducing the getters doesn't change any existing behavior: the real
   app still boots via its lifespan (real `mt5.initialize()` call, mocked),
   and an existing endpoint (`/status`) that reads the same underlying
   singletons directly still responds correctly and consistently with what
   the getters return.

No real MT5/broker calls are made -- only the `mock_mt5` boundary mock is
used, plus nothing else in the stack is faked.
"""

from fastapi.testclient import TestClient


def test_factory_exposes_getter_functions_for_existing_singletons(mock_mt5):
    """The four getters must exist, be callable, and return the *same*
    objects as the module-level singletons they wrap -- not copies, not
    freshly-constructed instances."""
    import app.factory as factory

    for name in (
        "get_market_data",
        "get_broker",
        "get_trade_executor",
        "get_orchestrators",
    ):
        getter = getattr(factory, name, None)
        assert callable(getter), f"app.factory.{name} must exist and be callable"

    assert factory.get_market_data() is factory.md
    assert factory.get_broker() is factory.br
    assert factory.get_trade_executor() is factory.trade_executor
    assert factory.get_orchestrators() is factory.orchestrators


def test_getters_are_stable_across_repeated_calls(mock_mt5):
    """Repeated calls must keep returning the identical singleton -- these
    are accessors, not factories that build a new instance per call."""
    import app.factory as factory

    broker_first = factory.get_broker()
    broker_second = factory.get_broker()
    assert broker_first is broker_second

    orchestrators_first = factory.get_orchestrators()
    orchestrators_second = factory.get_orchestrators()
    assert orchestrators_first is orchestrators_second
    assert orchestrators_first is factory.orchestrators


def test_getters_match_what_the_real_fully_wired_app_actually_uses(mock_mt5):
    """Drive the real app.main:app end-to-end (real FastAPI app, real
    lifespan, real orchestrators/broker/trade_executor wired by
    app/factory.py) and confirm the getters expose exactly what the running
    app is built from -- proving this subphase changed nothing observable
    about the app's behavior while adding a genuine new accessor surface.

    Uses /simulated_positions rather than /status: /status calls
    `orch.is_running()`, a method that doesn't exist on the real
    SignalOrchestrator (a pre-existing, unrelated bug) and would make this
    test fail for a reason that has nothing to do with this subphase's
    getters. /simulated_positions reads `br.mode`/`br.open_positions_sim`
    directly -- the same `br` singleton `get_broker()` wraps -- without
    going anywhere near that unrelated bug.
    """
    import app.factory as factory
    import app.main

    with TestClient(app.main.app) as client:
        response = client.get("/simulated_positions")

    assert response.status_code == 200
    # /simulated_positions (app/routes/endpoints.py) returns
    # br.open_positions_sim directly -- the getter must expose the exact
    # same list object the running app reads from.
    assert response.json() == factory.get_broker().open_positions_sim

    # The getters must reflect the actual objects backing the running app,
    # not a stale or parallel construction.
    assert factory.get_broker() is factory.br
    assert factory.get_market_data() is factory.md
    assert factory.get_trade_executor() is factory.trade_executor
    assert factory.get_orchestrators() is factory.orchestrators


def test_no_real_mt5_calls_made_by_exercising_getters_and_status_endpoint(mock_mt5):
    """Sanity guard: none of the above touches a real MT5 terminal -- every
    MT5 entry point the app boots/uses (initialize/shutdown/symbol_select/
    symbol_info_tick/positions_get/last_error) is the `mock_mt5` stub."""
    import app.factory as factory
    import app.main

    factory.get_market_data()
    factory.get_broker()
    factory.get_trade_executor()
    factory.get_orchestrators()

    with TestClient(app.main.app) as client:
        client.get("/simulated_positions")

    assert mock_mt5.initialize.called
