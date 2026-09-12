"""Tests for repository hygiene — stray files, dead code, and broken imports."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_backup_files_removed():
    """Backup copies of modules should not exist."""
    assert not (REPO_ROOT / "app/exit_strategies/managers/loss copy.py").exists()
    assert not (REPO_ROOT / "app/exit_strategies/managers/profit copy.py").exists()
    assert not (REPO_ROOT / "app/signals/indicators/sma_crossover copy.py").exists()
