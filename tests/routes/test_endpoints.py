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
