---
name: qa-verify
description: Two passes — author the full-stack e2e suite from requirements alone (runs in parallel with developer), then verify it plus the developer's tests against a plan's acceptance criteria (runs after developer's tests are green). Used by the qa agent.
---

## Author pass (parallel with `developer`, requirements-only)

1. Your prompt already contains this subphase's text and acceptance criteria — you don't need to open the plan file at all for this pass. Do not read `developer`'s in-progress diff for this subphase either. You're typically invoked at the same time as `developer`, sometimes before it has written anything, and the point of this pass is a black-box test derived from the spec, not from the implementation.
2. Write or extend an e2e test under `tests/e2e/` (mirroring the plan's phase/subphase structure, e.g. `tests/e2e/test_phase2_exit_fixes.py`) that will exercise this subphase's change through the real, fully-wired system — the real FastAPI app (`TestClient`), real `SignalOrchestrator`/`ExitTrade`/`Broker`/collectors — with only MT5 itself mocked at the boundary (`mock_mt5`). Don't fake collaborators that can run for real in-process; the point of this suite is to catch integration gaps unit tests can't see (wrong wiring in `app/factory.py`, a method name that doesn't actually exist on a real collaborator, a config value that doesn't reach the object that reads it). You may read already-merged code on `master` for existing routes/fixtures/interfaces needed to make the test executable.
3. Don't run this test against the subphase's real implementation yet — it may not exist or be finished during this pass. Report back that the author pass is done; the main session will bring you back for the verify pass once `developer` is green.
4. Never run `git add`/`git commit`/`git push` yourself in this pass — only edit files under `tests/e2e/`. `developer` may be editing `app/`/`tests/` on this same checkout at the same time; you touching git (not just the files) is what would turn that into a race. The main session stages/commits the combined result once both of you are done.

## Verify pass (after `developer`'s unit/integration tests are green)

1. Run the developer's unit/integration tests for the affected paths (`pipenv run pytest <affected test paths> -v`), then the e2e suite (`pipenv run pytest tests/e2e/ -v`), then the broader full suite (`pipenv run pytest`) if it's fast enough to be worth it.
2. Go through each subphase's acceptance criteria one by one. For each: does a test — unit/integration or e2e — actually exist that asserts it, and does that test assert the specific behavior described, not just that the code runs without error? A criterion covered only at the unit level but never exercised through the real wiring is a gap worth an e2e case, not just a pass.
3. Check explicitly for the edge cases this codebase tends to care about: `None`/missing tick or position fields, dict vs. object tick/position, zero volume, unknown symbol — flag it if the plan called one out and no test (unit, integration, or e2e) covers it.
4. Check no test, including every e2e test, calls real MT5 or a real broker.
5. Append a "QA" entry to the plan file (don't rewrite prior content) recording: date, subphases checked, pass/fail per subphase, what e2e coverage was added/extended, and a gap list if anything is missing or under-tested. To find where to append, `Grep` the file for the `## QA` heading and read just that tail (a targeted `Read` with an offset near the end) rather than reading the whole file from the top — you don't need earlier subphases' QA history to add a new entry.
6. Report a verdict to whoever invoked you: ready for `pr-reviewer` / phase ready for `docs/plans/done/` / send back to developer with the specific gaps listed.

`tests/e2e/**` and the plan file's QA section are yours to write and edit directly, in either pass. Don't edit anything else under `app/` or the rest of `tests/` — a gap in the developer's unit/integration tests goes back to the developer, not fixed here. Never run `git add`, `git commit`, `git push`, `gh pr create`, or `gh pr merge` — that's the main session's job. Read-only/diagnostic git commands (`diff`, `log`, `show`, a paired `stash`/`stash pop`) are fine, especially in the verify pass, for comparing behavior before and after a fix.
