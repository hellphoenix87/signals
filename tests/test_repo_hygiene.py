"""Tests for repository hygiene — stray files, dead code, and broken imports."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_backup_files_removed():
    """Backup copies of modules should not exist."""
    assert not (REPO_ROOT / "app/exit_strategies/managers/loss copy.py").exists()
    assert not (REPO_ROOT / "app/exit_strategies/managers/profit copy.py").exists()
    assert not (REPO_ROOT / "app/signals/indicators/sma_crossover copy.py").exists()


def test_dead_alternate_entrypoint_removed():
    """Dead alternate entrypoint main3.py and its only dependency should not exist."""
    assert not (REPO_ROOT / "app/main3.py").exists()
    assert not (REPO_ROOT / "app/utils/connection.py").exists()


def test_dead_process_handling_removed():
    """Dead process_handling.py module should not exist."""
    assert not (REPO_ROOT / "app/utils/process_handling.py").exists()


def test_no_process_handling_references():
    """No file should reference process_handling anymore."""
    # Search all .py files under app/ for "process_handling" substring
    app_dir = REPO_ROOT / "app"
    matches = []
    for py_file in app_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            matches.append(f"{py_file.relative_to(REPO_ROOT)} (unreadable)")
            continue
        if "process_handling" in content:
            matches.append(str(py_file.relative_to(REPO_ROOT)))
    assert not matches, "Found references to process_handling in:\n" + "\n".join(matches)
