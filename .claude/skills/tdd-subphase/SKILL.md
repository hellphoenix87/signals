---
name: tdd-subphase
description: Red-green-refactor loop for implementing exactly one subphase from a plan. Used by the developer agent.
---

Implement one subphase, following this loop:

1. **Red** — write the test(s) described by the subphase's acceptance criteria under `tests/`, in the path mirroring the `app/` module being changed. Run it (`pipenv run pytest <path> -v`) and confirm it fails, and fails for the expected reason (missing function/wrong behavior, not a typo or import error).
2. **Green** — write the minimum production code under `app/` to make the test pass. Don't implement anything the subphase didn't ask for.
3. **Refactor** — with tests green, clean up naming/duplication introduced by this subphase only. Don't refactor unrelated code.
4. **Confirm** — re-run the test file, and the rest of the affected module's tests if they exist, to make sure nothing else broke.

Mocking rules:

- Never call real MT5 functions or the real broker from a test. Use `unittest.mock`/`pytest-mock`, and prefer fixtures in `tests/conftest.py` (add one there if the needed mock doesn't exist yet — e.g. a fake tick object/dict, a fake position, a stub `Broker`).
- A "position" or "tick" in this codebase can be either a dict or an object depending on call site — if your test doubles need to support both, check `app/exit_strategies/exit_shared.py`'s `get_any()` for the existing pattern rather than inventing a new one.

If the subphase can't be completed as written — a dependency doesn't exist, the described behavior contradicts existing code — stop and report that instead of silently changing the approach.
