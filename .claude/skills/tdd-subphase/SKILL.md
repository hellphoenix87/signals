---
name: tdd-subphase
description: Red-green-refactor loop for implementing exactly one subphase from a plan, with unit/integration tests (not e2e — that's qa's job). Used by the developer agent.
---

Implement one subphase, following this loop. Scope is unit/integration tests only — the full-stack `tests/e2e/` suite belongs to the `qa` agent; don't write or touch it here. `qa` may be authoring e2e tests on this same checkout in parallel with you — that's expected and safe as long as you only edit files: never run `git add`/`git commit`/`git push`/`git checkout -b` yourself, since two agents running git concurrently on the same checkout is what would actually race, not the file edits. You're done once your own unit/integration tests are green; you don't run or wait for the e2e suite, and the main session handles all git operations and the eventual push/PR/merge.

1. **Red** — write the test(s) described by the subphase's acceptance criteria under `tests/` (never `tests/e2e/`), in the path mirroring the `app/` module being changed. Run it (`pipenv run pytest <path> -v`) and confirm it fails, and fails for the expected reason (missing function/wrong behavior, not a typo or import error).
2. **Green** — write the minimum production code under `app/` to make the test pass. Don't implement anything the subphase didn't ask for.
3. **Refactor** — with tests green, clean up naming/duplication introduced by this subphase only. Don't refactor unrelated code.
4. **Confirm** — re-run the test file, and the rest of the affected module's tests if they exist, to make sure nothing else broke.

Mocking rules:

- Never call real MT5 functions or the real broker from a test. Use `unittest.mock`/`pytest-mock`, and prefer fixtures in `tests/conftest.py` (add one there if the needed mock doesn't exist yet — e.g. a fake tick object/dict, a fake position, a stub `Broker`).
- A "position" or "tick" in this codebase can be either a dict or an object depending on call site — if your test doubles need to support both, check `app/exit_strategies/exit_shared.py`'s `get_any()` for the existing pattern rather than inventing a new one.

If the subphase can't be completed as written — a dependency doesn't exist, the described behavior contradicts existing code — stop and report that instead of silently changing the approach.
