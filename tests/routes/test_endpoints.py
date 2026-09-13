"""Tests for app/routes/endpoints.py.

Covers the dead commented-out endpoint block and the active route registry.
"""


def test_breakout_strategy_removed():
    """Assert the string 'BreakoutStrategy' no longer appears in endpoints.py source."""
    from pathlib import Path

    endpoints_path = Path(__file__).resolve().parents[2] / "app" / "routes" / "endpoints.py"
    source = endpoints_path.read_text(encoding="utf-8", errors="ignore")

    assert "BreakoutStrategy" not in source, (
        "The commented-out backtest_signals_endpoint_historical block "
        "still references BreakoutStrategy and should be removed"
    )


def test_router_has_exactly_ten_active_routes(mock_mt5):
    """Assert the router still contains exactly 10 active routes after removing dead code.

    This proves the deletion removed only dead comment text, not an active route.
    """
    from app.routes.endpoints import router

    # Compare (path, methods) pairs, not just paths, so a duplicate registration
    # on an existing path (which a set-of-paths comparison alone would miss) is
    # also caught.
    active_routes = {(route.path, frozenset(route.methods)) for route in router.routes}

    # Expected 10 routes per the plan
    expected_paths = {
        "/status",
        "/trading/start",
        "/trading/stop",
        "/signal/latest",
        "/live_signal",
        "/tick",
        "/simulated_positions",
        "/close_all",
        "/test_historical",
        "/stop_orchestrator",
    }

    assert len(router.routes) == len(expected_paths), (
        f"Expected exactly {len(expected_paths)} routes, found {len(router.routes)}: "
        f"{sorted(r.path for r in router.routes)}"
    )
    active_paths = {path for path, _methods in active_routes}
    assert active_paths == expected_paths, (
        f"Expected exactly {expected_paths}, but found {active_paths}"
    )


def test_backtest_signals_module_removed():
    """The backtest_signals helper module is now fully dead (its only caller was
    the removed BreakoutStrategy comment block) and should be gone, along with
    its import in endpoints.py.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / "app" / "utils" / "backtest_signals.py").exists()

    endpoints_path = repo_root / "app" / "routes" / "endpoints.py"
    source = endpoints_path.read_text(encoding="utf-8", errors="ignore")
    assert "backtest_signals" not in source


def test_endpoints_import_succeeds(mock_mt5):
    """Assert that importing app.routes.endpoints still succeeds after removing rm.

    This test ensures the removal of the unused rm import doesn't break the module.
    """
    # Import should succeed without any errors
    from app.routes import endpoints

    # Verify the endpoints module was loaded successfully
    assert hasattr(endpoints, 'router')
    assert hasattr(endpoints, 'get_status')


def test_rm_import_removed():
    """Assert that the unused rm identifier has been removed from endpoints.py.

    This test reads the file source and asserts a regex search for the standalone
    identifier \\brm\\b finds no matches.
    """
    import re
    from pathlib import Path

    endpoints_path = Path(__file__).resolve().parents[2] / "app" / "routes" / "endpoints.py"
    source = endpoints_path.read_text(encoding="utf-8", errors="ignore")

    # Assert that the standalone identifier 'rm' is not present
    # Use word boundaries to avoid matching 'perm', 'firm', etc.
    assert not re.search(r'\brm\b', source), (
        "The unused 'rm' identifier should be removed from the import in endpoints.py"
    )


def test_close_all_endpoint_calls_trade_executor_method(mock_mt5):
    """Test that POST /close_all calls trade_executor.close_all_trades() and returns 200.

    Given:
      - A mocked trade_executor

    When:
      - POST /close_all is called via TestClient

    Then:
      - trade_executor.close_all_trades is called exactly once
      - response status is 200
      - no AttributeError is raised
    """
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.endpoints import router
    from app.factory import get_trade_executor

    # Create a mock trade_executor with a realistic close_all_trades() return shape
    mock_trade_executor = MagicMock()
    mock_trade_executor.close_all_trades.return_value = {"closed": [1, 2], "failed": []}

    # Create the app and include the router
    app = FastAPI()
    app.include_router(router)

    # Set up dependency override for get_trade_executor
    app.dependency_overrides[get_trade_executor] = lambda: mock_trade_executor

    client = TestClient(app)

    # Act
    response = client.post("/close_all")

    # Assert
    assert response.status_code == 200
    assert mock_trade_executor.close_all_trades.call_count == 1
    body = response.json()
    assert body["status"] == "all trades closed"
    assert body["closed"] == [1, 2]
    assert body["failed"] == []


def test_close_all_endpoint_reports_failures():
    """POST /close_all should surface partial failures instead of always claiming success."""
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.endpoints import router
    from app.factory import get_trade_executor

    mock_trade_executor = MagicMock()
    mock_trade_executor.close_all_trades.return_value = {
        "closed": [1],
        "failed": [{"ticket": 2, "error": "close_position returned False"}],
    }

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_trade_executor] = lambda: mock_trade_executor

    client = TestClient(app)
    response = client.post("/close_all")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "some trades failed to close"
    assert body["closed"] == [1]
    assert body["failed"] == [{"ticket": 2, "error": "close_position returned False"}]


def test_status_endpoint_uses_dependency_injection(mock_mt5):
    """Test that GET /status uses FastAPI dependency injection and does not require real app.factory singletons.

    This test verifies the acceptance criteria: endpoints can work with mocked dependencies
    via FastAPI's dependency_overrides without needing the real app.factory singletons.

    Given:
      - A FastAPI app with the router
      - Dependency overrides set for get_orchestrators and get_trade_executor
      - Mock orchestrators with is_running() mocked
      - Mock trade_executor with daily_profit/last_reset attributes

    When:
      - GET /status is called

    Then:
      - Response is 200
      - Response JSON contains orchestrator_running with mocked values
      - Real app.factory singletons were never accessed
    """
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.endpoints import router
    from app.factory import get_orchestrators, get_trade_executor

    # Create mock orchestrator
    mock_orchestrator = MagicMock()
    mock_orchestrator.is_running.return_value = True

    # Create mock trade_executor
    mock_trade_executor = MagicMock()
    mock_trade_executor.daily_profit = 123.45
    mock_trade_executor.last_reset = "2025-01-01"

    # Create app and include router
    app = FastAPI()
    app.include_router(router)

    # Set up dependency overrides
    app.dependency_overrides[get_orchestrators] = lambda: {"EURUSD": mock_orchestrator}
    app.dependency_overrides[get_trade_executor] = lambda: mock_trade_executor

    # Make request
    client = TestClient(app)
    response = client.get("/status")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["orchestrator_running"] == {"EURUSD": True}
    assert data["daily_profit"] == 123.45
    assert data["last_reset"] == "2025-01-01"

    # Verify mocks were called
    assert mock_orchestrator.is_running.call_count >= 1
    assert mock_trade_executor is not None


def test_trading_start_endpoint_uses_dependency_injection(mock_mt5):
    """Test that POST /trading/start uses dependency injection."""
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.endpoints import router
    from app.factory import get_orchestrators

    # Create mock orchestrator
    mock_orchestrator = MagicMock()
    mock_orchestrator.start = MagicMock()

    # Create app and include router
    app = FastAPI()
    app.include_router(router)

    # Set up dependency override
    app.dependency_overrides[get_orchestrators] = lambda: {"EURUSD": mock_orchestrator}

    # Make request
    client = TestClient(app)
    response = client.post("/trading/start")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "trading started"
    assert data["orchestrator_running"] is True
    assert mock_orchestrator.start.call_count == 1


def test_simulated_positions_endpoint_uses_dependency_injection(mock_mt5):
    """Test that GET /simulated_positions uses dependency injection."""
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.endpoints import router
    from app.factory import get_broker

    # Create mock broker
    mock_broker = MagicMock()
    mock_broker.open_positions_sim = {"pos1": {"ticket": 1, "volume": 0.01}}
    mock_broker.mode = MagicMock()

    # Create app and include router
    app = FastAPI()
    app.include_router(router)

    # Set up dependency override
    app.dependency_overrides[get_broker] = lambda: mock_broker

    # Make request
    client = TestClient(app)
    response = client.get("/simulated_positions")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data == {"pos1": {"ticket": 1, "volume": 0.01}}


def test_test_historical_endpoint_uses_dependency_injection(mock_mt5):
    """Test that GET /test_historical uses dependency injection."""
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routes.endpoints import router
    from app.factory import get_market_data

    # Create mock market_data
    mock_market_data = MagicMock()
    mock_market_data.get_historical_candles.return_value = [
        {"close": 1.1000, "open": 1.0990},
        {"close": 1.1005, "open": 1.1000},
    ]

    # Create app and include router
    app = FastAPI()
    app.include_router(router)

    # Set up dependency override
    app.dependency_overrides[get_market_data] = lambda: mock_market_data

    # Make request
    client = TestClient(app)
    response = client.get("/test_historical")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert "candles" in data
    assert len(data["candles"]) == 2
    assert mock_market_data.get_historical_candles.call_count == 1
