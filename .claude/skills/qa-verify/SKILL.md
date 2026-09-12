---
name: qa-verify
description: Run tests and cross-check results against a plan's acceptance criteria. Used by the qa agent after the developer agent reports a subphase or phase complete.
---

1. Run the tests for the affected paths: `pipenv run pytest <affected test paths> -v`, then the broader suite (`pipenv run pytest`) if it's fast enough to be worth it.
2. Open the plan file (`docs/plans/in-progress/<slug>.md`) and go through each subphase's acceptance criteria one by one. For each: does a test actually exist that asserts it, and does that test assert the specific behavior described — not just that the code runs without error?
3. Check explicitly for the edge cases this codebase tends to care about: `None`/missing tick or position fields, dict vs. object tick/position, zero volume, unknown symbol — flag it if the plan called one out and no test covers it.
4. Check no test calls real MT5 or a real broker.
5. Append a "QA" entry to the plan file (don't rewrite prior content) recording: date, subphases checked, pass/fail per subphase, and a gap list if anything is missing or under-tested.
6. Report a verdict to whoever invoked you: proceed to next subphase / phase ready for `docs/plans/done/` / send back to developer with the specific gaps listed.

Don't edit anything under `app/` or `tests/` — only the plan file's QA section.
