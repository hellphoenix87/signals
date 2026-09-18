# Post-Breakeven Trailing-Stop Rule Simulation

**Question**: `ProfitExitManager.check_exit_on_tick`/`check_exit_on_candle_close` hardcode a `$0.04` trailing pullback, disconnected from `Config` entirely. `--measure-post-be` (`pre-breakeven-exit-strategy-backtest.md`'s sibling, run separately across the same 7 pairs x 3 windows) confirmed real post-breakeven drawdown-from-peak has a **p10 of $3.47** -- roughly 85x the current gap -- so the live gap isn't really a trailing stop, it's closer to "exit on the first tick of pullback." Given that, would an actual, properly-sized trailing rule have captured more real profit than either the current near-zero gap, or no trail at all?

## Method

New `--simulate-trail` flag on `scripts/backtest_exit_strategy.py` (see Thread 3 of `docs/exit-strategy-open-threads.md`). For every `reached_be` trade (breakeven armed within the pre-breakeven window, same real-tick-replay method as the pre-breakeven backtest), continues the *same* real tick stream past arming and replays five candidate trailing-gap rules in a single pass, sharing one running peak-profit tracker:

- `flat5` / `flat7` / `flat9` -- exit when profit drops to `peak - $5` / `$7` / `$9`
- `pct40_floor2` / `pct60_floor2` -- exit when profit drops to `peak - max($2, 40%/60% of peak)` (a $2 floor so a small peak isn't stopped by trivial noise)

Each rule only becomes active once its own computed trigger (`peak - gap`) turns positive -- while peak hasn't grown past the gap yet, the trail stays inactive rather than force-exiting the moment the trigger math goes negative (a first implementation clamped to a $0 floor instead and triggered nearly every trade within ~4 ticks; fixed before this run -- see commit history). A trade that reverses below breakeven before any rule ever engages is Thread 4's territory (the post-BE loss cap), not simulated here.

Run across the same 7 majors (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD) x 3 independent non-overlapping 4-week windows as the rest of this project's exit-strategy work, with `--measure-post-be` in the same pass (one tick fetch covers both).

## Results

**Pooled across all 21 pair/window combinations, 2,818 `reached_be` trades:**

| Rule | Total P&L | vs. "hold forever" | Win rate | Triggered | Avg ticks held |
|---|---|---|---|---|---|
| *(reference) hold forever, no exit* | **-$1,024.44** | -- | -- | -- | -- |
| *(reference) exit exactly at peak* | +$12,768.70 | +$13,793.14 | -- | -- | -- |
| `flat5` | -$1,055.81 | -$31.37 | 49.6% | 508/2818 (18.0%) | 2777.3 |
| `flat7` | -$1,143.51 | -$119.07 | 47.2% | 175/2818 (6.2%) | 2920.9 |
| `flat9` | -$1,056.75 | -$32.31 | 46.7% | 80/2818 (2.8%) | 2966.6 |
| `pct40_floor2` | -$872.76 | +$151.68 | 61.0% | 1722/2818 (61.1%) | 1815.4 |
| **`pct60_floor2`** | **-$700.90** | **+$323.54** | 61.0% | 1573/2818 (55.8%) | 1937.1 |

("exit exactly at peak" is an unachievable oracle reference -- no rule can know the peak until after it's passed -- included only to show how much upside exists in principle, not as a realistic target.)

**Per-pair (`pct60_floor2` vs. holding forever):**

| Pair | n | `pct60_floor2` | hold forever | Difference |
|---|---|---|---|---|
| EURUSD | 390 | -$79.20 | -$80.20 | +$1.00 |
| GBPUSD | 338 | -$67.80 | -$93.80 | +$26.00 |
| USDJPY | 557 | -$120.85 | -$246.49 | +$125.64 |
| AUDUSD | 384 | -$193.20 | -$226.60 | +$33.40 |
| USDCHF | 325 | -$254.20 | -$276.11 | +$21.91 |
| USDCAD | 369 | -$29.45 | +$56.16 | **-$85.61** |
| NZDUSD | 455 | +$43.80 | -$157.40 | +$201.20 |

## Findings

1. **Scaling beats flat, decisively.** All three flat-dollar rules (`flat5`/`flat7`/`flat9`) *underperform* just holding forever with no exit at all. Both percentage-of-peak rules beat it, and by a growing margin as the percentage loosens (`pct60_floor2` > `pct40_floor2`). This directly answers Thread 3's open fixed-vs-scaling question: on this account's real tick data, a gap that grows with peak profit outperforms a constant-dollar gap.

2. **Even the best candidate is a modest win, not a breakthrough.** `pct60_floor2`'s +$323.54 improvement over the no-exit baseline, spread across 2,818 trades, is small on a per-trade basis (~$0.11/trade) -- real, but not the kind of result that changes the "is the bot profitable" question on its own. The gap between any tested rule and the oracle peak-ceiling (+$12,768.70) is enormous, meaning there's a lot of theoretical room a wiser rule (or an adaptive one) could still capture -- these five candidates were a deliberately bounded first pass, not an exhaustive search.

3. **The flat rules' failure mode matches the current bug's failure mode, just less extreme.** `flat5`/`flat7`/`flat9` trigger far less often than the percentage rules (2.8%-18.0% of trades vs. 55.8%-61.1%) but still lose to doing nothing -- because a constant-dollar gap is either too tight for large-peak trades (giving back a big chunk of a large gain) or effectively never engages for the majority of trades that never build a big enough peak to clear a flat $5-9 trigger at all (consistent with the pooled median peak profit of $3.40 from `--measure-post-be` -- most trades never reach a flat-$5 gap's activation threshold in the first place).

4. **Not unanimous per pair.** 6 of 7 pairs improve with `pct60_floor2`; USDCAD is the one exception (-$85.61 worse). USDCAD is also the only pair where "hold forever" was already net profitable (+$56.16) -- consistent with a trail cutting some of USDCAD's bigger winners short before they fully matured, the classic trail-stop tradeoff (protects the downside on losers, caps some upside on winners).

## Answers to Thread 3's open sub-questions

- **(a) Minimum profit floor before the trail engages?** Yes, and it falls out of the rule design itself rather than needing a separate parameter: a rule only becomes active once its own trigger (`peak - gap`) is positive. No extra floor parameter needed.
- **(b) Interaction with Thread 4's loss cap?** Confirmed clean separation: while a rule is inactive (trigger not yet positive), a trade that reverses below breakeven is untouched by this trail and falls entirely to Thread 4's mechanism -- no overlap or conflict simulated or expected.

## Caveats

- **"Hold forever" is a reference floor, not a real alternative** -- in production some exit eventually happens (currently the near-zero $0.04 gap); this comparison isolates the trail-rule question but doesn't simulate the full current production behavior post-breakeven for a three-way comparison.
- **Only 5 candidate rules tested**, chosen to bracket the flat-vs-scaling question cheaply, not to find an optimum. A natural next step (not done here) would be a finer sweep around `pct60_floor2` (e.g. 50%/70%/80% floors, different floor dollar amounts) now that scaling is known to be the right shape.
- Same lot-size/entry-price/account-specific-spread caveats as `pre-breakeven-exit-strategy-backtest.md` apply unchanged.
- Wiring any of this into `ProfitExitManager`/`Config` is a separate step, not done as part of this measurement.
