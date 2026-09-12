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


def test_mt5_smoke_script_moved():
    """MT5 smoke test should be moved to scripts/ and removed from root."""
    assert not (REPO_ROOT / "test_mt5.py").exists()
    assert (REPO_ROOT / "scripts/mt5_smoke.py").exists()


def test_no_stray_test_files_in_repo_root():
    """No pytest-collectible files (test_*.py or *_test.py) should exist in repo root.

    Both glob patterns are checked because pytest's default python_files setting
    (pytest.ini here only overrides testpaths, not python_files) collects either
    shape -- a file matching *_test.py at the repo root is just as much a hazard
    as one matching test_*.py if a test runner or IDE is ever pointed at the repo
    root directly instead of the shielded `testpaths = tests` default.
    """
    stray_test_files = []
    for item in REPO_ROOT.iterdir():
        if not item.is_file() or item.suffix != ".py":
            continue
        if item.name.startswith("test_") or item.name.endswith("_test.py"):
            stray_test_files.append(str(item.relative_to(REPO_ROOT)))
    assert not stray_test_files, "Found stray pytest-collectible files in repo root:\n" + "\n".join(
        stray_test_files
    )


def test_mt5_reference_updated_in_claude_md():
    """CLAUDE.md should reference the new MT5 smoke test location, not the old one."""
    claude_md = REPO_ROOT / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text(encoding="utf-8")
    assert "test_mt5.py" not in content, "CLAUDE.md should not reference test_mt5.py"
    assert "scripts/mt5_smoke.py" in content, "CLAUDE.md should reference scripts/mt5_smoke.py"


def test_entry_filter_removed_from_docs():
    """Documentation should not reference the stale entry_filter.py file."""
    claude_md = REPO_ROOT / "CLAUDE.md"
    readme_md = REPO_ROOT / "README.md"

    assert claude_md.exists()
    assert readme_md.exists()

    claude_content = claude_md.read_text(encoding="utf-8")
    readme_content = readme_md.read_text(encoding="utf-8")

    assert "entry_filter.py" not in claude_content, "CLAUDE.md should not reference entry_filter.py"
    # README never wrote the ".py" suffix -- it read "entry filter calculations" (space, no
    # extension), so the check above would pass vacuously against README. Assert the actual
    # wording that was there instead.
    assert "entry filter" not in readme_content, "README.md should not reference entry filter"


def test_main3_removed_from_docs():
    """CLAUDE.md should not contain the old reference to main3.py as a separate entrypoint."""
    claude_md = REPO_ROOT / "CLAUDE.md"
    assert claude_md.exists()

    content = claude_md.read_text(encoding="utf-8")
    # The actual prose wraps the path in backticks (`app/main3.py`), so a check for the
    # unbacktick'd substring above would pass vacuously even against the pre-change file.
    assert "is a separate/alternate entrypoint" not in content, (
        "CLAUDE.md should not describe main3.py as a separate/alternate entrypoint"
    )
    assert "sole entrypoint" in content, (
        "CLAUDE.md should state app.main:app is the sole entrypoint"
    )
