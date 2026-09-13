---
name: qa-verify
description: Write/extend the full-stack e2e suite, run it plus the developer's tests, and cross-check both against a plan's acceptance criteria. Used by the qa agent after the developer agent reports a subphase or phase complete.
---

1. Open the plan file (`docs/plans/in-progress/<slug>.md`) and read the subphase(s) just implemented, plus its acceptance criteria.
2. Write or extend an e2e test under `tests/e2e/` (mirroring the plan's phase/subphase structure, e.g. `tests/e2e/test_phase2_exit_fixes.py`) that exercises this subphase's change through the real, fully-wired system — the real FastAPI app (`TestClient`), real `SignalOrchestrator`/`ExitTrade`/`Broker`/collectors — with only MT5 itself mocked at the boundary (`mock_mt5`). Don't fake collaborators that can run for real in-process; the point of this suite is to catch integration gaps unit tests can't see (wrong wiring in `app/factory.py`, a method name that doesn't actually exist on a real collaborator, a config value that doesn't reach the object that reads it).
3. Run the developer's unit/integration tests for the affected paths (`pipenv run pytest <affected test paths> -v`), then the e2e suite (`pipenv run pytest tests/e2e/ -v`), then the broader full suite (`pipenv run pytest`) if it's fast enough to be worth it.
4. Go through each subphase's acceptance criteria one by one. For each: does a test — unit/integration or e2e — actually exist that asserts it, and does that test assert the specific behavior described, not just that the code runs without error? A criterion covered only at the unit level but never exercised through the real wiring is a gap worth an e2e case, not just a pass.
5. Check explicitly for the edge cases this codebase tends to care about: `None`/missing tick or position fields, dict vs. object tick/position, zero volume, unknown symbol — flag it if the plan called one out and no test (unit, integration, or e2e) covers it.
6. Check no test, including every e2e test, calls real MT5 or a real broker.
7. Append a "QA" entry to the plan file (don't rewrite prior content) recording: date, subphases checked, pass/fail per subphase, what e2e coverage was added/extended, and a gap list if anything is missing or under-tested.
8. Report a verdict to whoever invoked you: proceed to next subphase / phase ready for `docs/plans/done/` / send back to developer with the specific gaps listed.

`tests/e2e/**` and the plan file's QA section are yours to write and edit directly. Don't edit anything else under `app/` or the rest of `tests/` — a gap in the developer's unit/integration tests goes back to the developer, not fixed here.
