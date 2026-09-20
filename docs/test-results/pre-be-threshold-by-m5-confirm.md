# Pre-Breakeven Threshold Split by M5-Confirm State

Date: 2026-09-20

## Setup

- **Pairs/windows**: same 7 majors, 3 non-overlapping 4-week windows each, used throughout the exit-strategy investigation (21 combinations, 6,990 trades pooled).
- **Method**: `scripts/backtest_exit_strategy.py --sweep-pre-be-threshold` (new capability built for this plan) -- for every real entry, replays candidate pre-breakeven soft-SL money thresholds (`$2/$3/$4/$5/$7/$10`; `$5` is today's live `Config.EXIT_MAX_LOSS_MONEY`) against the same real tick stream in one pass, each a genuine `LossExitManager` instance (not a reimplementation of its state machine). Verified to reproduce a plain single-threshold run's outcome/profit/ticks trade-for-trade before trusting it (0 mismatches, 40-trade smoke test).
- **Bug caught and fixed before trusting the analysis**: `m5_confirm` is a raw direction string (`"hold"`/`"buy"`/`"sell"`), not a boolean -- confirmation means it equals the trade's own `direction`, not merely "not hold." An earlier draft of the analysis script conflated "non-hold" with "confirms," which would have silently swapped the two groups. Fixed and verified against a sample file's known counts before running the real analysis.

## Results: pooled (all 7 pairs, all 3 windows)

| Group | n | Current ($5) P&L | Best candidate | Best P&L | Gain if switched |
|---|---|---|---|---|---|
| All trades | 6,990 | -$1,541.62 | $10 | -$1,466.26 | +$75.36 |
| M5 confirms | 1,469 (21%) | -$247.94 | $10 | -$223.98 | +$23.96 |
| M5 does NOT confirm | 5,521 (79%) | -$1,293.69 | $2 | -$1,209.60 | +$84.09 |

At face value this matches Thread 2's original hypothesis exactly (tighter for non-confirm, looser for confirm), and a split ($2 for non-confirm + $10 for confirm = -$1,433.58) would beat even the best single uniform threshold ($10 flat, -$1,466.26) by a further $32.69.

## Results: per-pair reproducibility check

Pooling can hide a result driven by one pair (the same lesson from Thread 1's per-window check). Best threshold and gain-vs-$5, computed separately per pair (each pair's own 3-window pool):

| Pair | M5 confirms: best (gain) | M5 doesn't confirm: best (gain) |
|---|---|---|
| EURUSD | $7 (+$0.80) | $7 (+$4.40) |
| GBPUSD | $2 (+$2.40) | **$10 (+$20.40)** |
| USDJPY | $7 (+$10.96) | $2 (+$19.45) |
| AUDUSD | $4/no change (+$0.00) | $5/no change (+$0.00) |
| USDCHF | $5/no change (+$0.00) | $2 (+$29.28) |
| USDCAD | $3/no change (+$0.00) | $2 (+$13.55) |
| NZDUSD | **$10 (+$39.40)** | $2 (+$45.20) |

## Findings

1. **The "M5 confirms -> looser threshold" signal does not reproduce.** The pooled result ($10 best, +$23.96) is driven almost entirely by one pair (NZDUSD, +$39.40 -- larger than the entire pooled gain on its own). Best threshold varies all over the map per pair (2, 3, 4, 5, 7, 7, 10) with no consistent direction, and GBPUSD's own best threshold ($2) is the *opposite* direction from the pooled recommendation. This is noise, not signal.
2. **The "M5 does NOT confirm -> tighter threshold" signal is more consistent but still not unanimous.** 5 of 7 pairs improve with some threshold change (GBPUSD, USDJPY, USDCHF, USDCAD, NZDUSD), and 4 of those 5 specifically favor the tightest candidate tested ($2). But GBPUSD's own best is $10 -- the opposite direction -- and AUDUSD shows no preference at all. A real majority pattern, not a unanimous one.
3. **Everything here is pre-BE-phase P&L only**, consistent with Thread 2's own scope (post-breakeven behavior, where Threads 3/4 already found the system is still net negative overall, is not simulated by this test).

## Verdict

**Not actionable as a hard-coded M5-confirm split.** The confirm-side signal doesn't survive a per-pair check at all, and the non-confirm-side signal, while more real, isn't unanimous enough to hard-code a specific threshold value with confidence -- exactly the kind of result Thread 2 was scoped to allow for from the start ("bounded ceiling... a decision on wiring is made once the data is in, not pre-committed"). No `Config`/`LossExitManager` change follows from this analysis.

**What would be worth revisiting, if resumed**: the non-confirm-side tightening pattern (5/7 pairs, mostly favoring $2) is the more promising half of this result and could be worth a finer sweep (e.g. $1.5/$2/$2.5/$3) isolated to that group alone, without also trying to force a confirm-side split that the data doesn't support. Not pursued further here -- the signal isn't strong enough yet to justify the added complexity of a conditional threshold in production.
