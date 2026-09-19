# Post-Breakeven Loss Cap (Thread 4)

**Question**: `LossExitManager`'s post-breakeven branch hardcoded `drop_profit_after_be = -5` -- if a position that already armed breakeven reverses and drops to -$5 or below, force-close. Testing Thread 3's trail-rule candidates (`post-breakeven-trail-simulation.md`) surfaced that this cap fires in the "gap" before any trail rule ever activates, for a large fraction of trades. Is `-$5` actually the right value, or is it (like the pre-breakeven soft-SL and the post-breakeven trailing gap before it) another hardcoded number nobody had checked against real data?

## Method

New `--simulate-trail` output on `scripts/backtest_exit_strategy.py` (Thread 4 additions, `post-be-loss-cap` plan/branch), reusing the same real-tick-replay technique as the rest of this project's exit-strategy backtests:

1. **Real loss-manager combination**: `simulate_trail_rules` now also calls the real, unmodified `LossExitManager.check_exit_on_tick` every tick after breakeven arms (same `state` object, already `be_armed=True`), so the trail candidates' outcomes reflect the real safety net running underneath them rather than an unrealistic "hold forever, zero protection" baseline.
2. **Gap-reversal counterfactual**: for every trade the real cap force-closes, continues watching the same real tick stream from the exact breach point onward with no exit rule, to measure whether it would have recovered (`gap_recovered`), how long that took, and the worst further drawdown before any recovery.
3. **`CAP_THRESHOLDS` candidates** (`-$3, -$5, -$7, -$10, -$15`): five independent flat "exit the instant profit drops to/below this value" rules, replayed against the same raw profit series in the same single pass -- `-5.0` included as the pre-retune production baseline.

Run across the same 7 majors (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD) x 3 independent non-overlapping 4-week windows as the rest of this project's exit-strategy work.

## Results

### 1. How often does the real cap fire, and where

Pooled across 2,754 `reached_be` trades:

| | Count | % |
|---|---|---|
| Real `-$5` cap fires (any reason) | 1,134 | 41.2% |
| ...as the `-$5` force-close (`profit_drop_after_be`) | 1,134 | 41.2% |
| ...as the marginal-recovery lock-in (`be_recovered_after_unprofit`) | 0 | 0.0% |

The marginal-recovery lock-in **never fired once** across the full pooled sample -- dead weight under current parameters, left unchanged (not retuned, no evidence it needs to be).

Per-pair fire rate ranges from 30.5% (USDCAD) to 54.3% (USDCHF).

**100% of firings happen in the gap** -- before any Thread 3 trail candidate (`flat5`/`flat7`/`flat9`/`pct40_floor2`/`pct60_floor2`) has grown its peak past its own activation threshold. Confirmed at full pooled scale, not just the single-pair smoke test that first surfaced it.

### 2. Gap-reversal counterfactual (retrospective: was catching these specific trades at -$5 right?)

For the 1,134 trades the real `-$5` cap caught:

| | Value |
|---|---|
| Recovered to positive profit if left alone | 321 (28.3%) |
| Did not recover | 813 (71.7%) |
| Real total (with `-$5` cap) | -$6,469.75 |
| If cap didn't exist (left alone to window end) | -$5,528.97 |
| Difference | -$940.77 (cap **hurt** this specific subset) |
| Worst further drawdown after cap-point (median / p10) | -$7.96 / -$16.44 |

Removing the cap entirely would have modestly improved this specific already-capped subset's pooled total, at the cost of materially larger tail losses (up to -$16.44 at the 10th percentile, more than 3x the current floor). A real tradeoff, not a clean bug.

**This number should not be read as "loosen the cap"** -- see below for why.

### 3. The decision-relevant test: candidate thresholds as independent, population-wide rules

Unlike (2), which only re-examines the trades already selected by the current `-$5` rule, this replays each candidate threshold as if it were the *actual* rule from the start, across all 2,826 `reached_be` trades (whether or not they'd have hit `-$5` under the old rule):

| Threshold | Total P&L | Win rate | Triggered | Avg ticks to cap |
|---|---|---|---|---|
| **-$3** | **-$1,331.87** | 32.9% | 1,691/2,826 (59.8%) | 1,812.7 |
| -$5 *(pre-retune production)* | -$1,903.89 | 40.6% | 1,130/2,826 (40.0%) | 2,336.2 |
| -$7 | -$2,349.20 | 43.3% | 686/2,826 (24.3%) | 2,637.3 |
| -$10 | -$2,454.77 | 44.6% | 341/2,826 (12.1%) | 2,834.4 |
| -$15 | -$2,437.59 | 45.4% | 138/2,826 (4.9%) | 2,935.2 |

**`-$3` wins pooled by $572.02 over `-$5`, and in 6 of 7 pairs** -- only EURUSD favors a looser cap (there, `-$15` beats `-$3` by a wide margin: -$76.00 vs -$265.20; every other pair worsens monotonically as the cap loosens).

**Why this doesn't contradict (2)**: (2) asked a narrow, retrospective question about one fixed, already-selected subset. (3) asks what actually matters for choosing a rule -- tightening the cap changes *which* trades get caught, not just what happens to the same fixed set. Most trades that grind down toward -$5 keep grinding rather than recovering, so catching them earlier at -$3 saves more than the occasional recovery it forecloses, even though (2) shows that *specific* recoveries do happen.

## Decision

**Wired**: `Config.EXIT_POST_BE_LOSS_CAP_MONEY` (new field, default `3.0`) replaces the hardcoded `-5` in `LossExitManager`, via `ExitTradeConfig.post_be_loss_cap_money`. Verified end-to-end with a smoke test -- the real loss manager's output now matches the independent `-$3` candidate's numbers exactly.

## Caveats / open follow-ups

- **`-$3` was the tightest candidate tested and won outright** -- the trend from `-$3` to `-$15` is close to monotonically worse as the cap loosens, suggesting `-$3` may not be the true optimum. Untested: `-$1`/`-$2`.
- **Recovery-rate breakdown only exists for the real `-$5` rule**, not for `-$3`/`-$7`/`-$10`/`-$15` individually. It's not yet known whether `-$3` wins mainly on better precision (correctly cutting fewer true recoveries) or mainly on avoiding tail risk while cutting a similar or higher fraction of real recoveries -- the aggregate total nets this out either way, but the distinction matters for how much to trust the choice going forward.
- **EURUSD is a consistent exception** -- every other pair prefers tighter, EURUSD prefers much looser. Not investigated further; `Config.SYMBOLS` is EURUSD-only in production today, which is worth flagging as a tension with the pooled recommendation (the live pair is the one pair that disagreed with it).
- Same lot-size/entry-price/account-specific-spread caveats as the rest of this project's exit-strategy backtests apply unchanged.
