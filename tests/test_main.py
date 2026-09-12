"""Tests for the canonical app entrypoint app.main."""

from fastapi import FastAPI


def test_main_import_succeeds(mock_mt5):
    """The canonical entrypoint app.main should import without errors."""
    import app.main

    assert app.main is not None


def test_main_app_is_fastapi_instance(mock_mt5):
    """app.main.app should be a FastAPI instance."""
    import app.main

    assert isinstance(app.main.app, FastAPI)
