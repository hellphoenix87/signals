# Tight entry spread gate (Test N)

## Goal

The pre-BE timeout is ~90% of net loss, and entry spread is its dominant predictor. Test whether
gating entries at 1 point (0.1 pip) beats the shipped 1.0-pip gate on 12 unseen windows, in four
sequential stages (4 -> 2 -> 4 -> 2 windows), stopping on a failed stage.

## Phases

### Phase 1: Log entry spread, pre-register -- DONE
- `scripts/backtest_exit_strategy.py` writes `entry_spread_pips` per trade, so one ungated run per
  window grades any spread threshold. Test N pre-registered before any W13-W24 data was run.

### Phase 2: Stage 1 -- W13, W16, W19, W22 (direction) -- PASS 4/4 (bar >= 3)
- W13 (median spread 0.1p): TIGHT -$0.152/tr vs SHIPPED -$0.505 (427 vs 2,694 trades), -$65 vs -$1,362.
- W22 (median 0.4p): TIGHT abstains (10 trades, -$2) vs SHIPPED 3,797 trades at -$0.935 = -$3,551.
- W16, W19 (median 1.3p): the 1.0p gate ALREADY abstains (44 / 95 trades); TIGHT 1 trade each. Wins on
  total, but near-trivial -- both arms sat those regimes out.
- Pooled: SHIPPED 6,630 tr -$5,272 (-$0.795/tr); TIGHT 439 tr -$91 (-$0.207/tr).
- Broker spread regime shifts over time: 1.3p (Apr/Jul 2025) -> 0.4p (Jan 2025) -> 0.1p (Oct 2025)
  -> 0.0p (Sep 2026, live today).
### Phase 3: Stage 2 -- W14, W20 (confirm or decline) -- PASS, cumulative 6/6 (bar >= 5)
- W14: TIGHT -$0.567/tr vs SHIPPED -$0.575 (299 vs 1,670 trades) -- essentially a TIE on per-trade;
  the win is almost all from trading 18% as often (-$170 vs -$960).
- W20: TIGHT abstains (45 trades, +$2.80) vs SHIPPED 1,497 trades at -$0.697 = -$1,043.
- Cumulative: SHIPPED 9,797 tr -$7,275 (-$0.743/tr); TIGHT 783 tr -$258 (-$0.329/tr).
- Reading so far: the gain is mostly ABSTENTION from wide-spread regimes. When TIGHT does trade it is
  still negative (W13 -$0.15, W14 -$0.57/tr).
### Phase 4: Stage 3 -- W15, W17, W21, W23 -- PASS, cumulative 10/10 (bar >= 8)
- All four graded on total: TIGHT made 63 / 1 / 55 / 14 trades (0-14% kept) vs SHIPPED 457 / 55 /
  3,488 / 3,350. SHIPPED lost -$338 / -$132 / -$2,527 / -$2,938; TIGHT -$9 / -$1 / -$20 / -$12.
- Cumulative: SHIPPED 17,147 tr -$13,209 (-$0.770/tr); TIGHT 916 tr -$300 (-$0.328/tr).
### Phase 5: Stage 4 -- W18, W24; write docs/test-results/tight-spread-gate.md -- PASS, 12/12 (bar >= 9)
- W18: TIGHT -$0.953/tr vs -$1.422 (198 vs 255 trades). W24: TIGHT abstains (6 trades) vs -$3,321.
- Final pooled: SHIPPED 21,123 tr -$16,893 (-$0.800/tr); TIGHT 1,120 tr -$496 (-$0.443/tr).
- Write-up: docs/test-results/tight-spread-gate.md.

Each stage: one unfiltered run per window, grade against the pre-registered cumulative bar,
commit + push, report. A failed stage ends the plan.

## Open questions
- On PASS: ship `MAX_SPREAD_POINTS = 1.5`? User decision; demo-vs-real spread caveat applies.
