# Tight entry spread gate (Test N)

## Goal

The pre-BE timeout is ~90% of net loss, and entry spread is its dominant predictor. Test whether
gating entries at 1 point (0.1 pip) beats the shipped 1.0-pip gate on 12 unseen windows, in four
sequential stages (4 -> 2 -> 4 -> 2 windows), stopping on a failed stage.

## Phases

### Phase 1: Log entry spread, pre-register -- DONE
- `scripts/backtest_exit_strategy.py` writes `entry_spread_pips` per trade, so one ungated run per
  window grades any spread threshold. Test N pre-registered before any W13-W24 data was run.

### Phase 2: Stage 1 -- W13, W16, W19, W22 (direction)
### Phase 3: Stage 2 -- W14, W20 (confirm or decline)
### Phase 4: Stage 3 -- W15, W17, W21, W23
### Phase 5: Stage 4 -- W18, W24; write docs/test-results/tight-spread-gate.md

Each stage: one unfiltered run per window, grade against the pre-registered cumulative bar,
commit + push, report. A failed stage ends the plan.

## Open questions
- On PASS: ship `MAX_SPREAD_POINTS = 1.5`? User decision; demo-vs-real spread caveat applies.
