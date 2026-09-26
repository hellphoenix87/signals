# Exit Tuning Has Hit Its Limit -- Next Focus: Signal Quality and Entry Timing

Date: 2026-09-26. Covers the exit tests run after the wait-for-zero-spread entry shipped (PR #76),
a trade-phase breakdown of the live config, and what the evidence says about where the remaining
loss comes from. Written as the hand-off for the next line of work.

**TL;DR**

- With the live config, **no trade loses money before breakeven any more**. The remaining loss is
  entirely post-breakeven: a near-binary outcome of about -$1.18 (the $1 cap) or about +$2.22 (the
  staircase trail).
- Every tightening of the post-BE exit tested on the new config -- cap at breakeven, a +$0.20 lock,
  a +$1 lock -- **lost more than the current $1 cap / $2 staircase** in both windows. They save
  losers but cut winners faster. The exits look like a local optimum for this entry.
- The system breaks even when **34.8%** of trades reach the staircase; it is at **33.5%**. A
  **+1.3 percentage point** improvement in how often an entry follows through flips the sign. That is
  a signal-quality problem, not an exit problem.
- 24% of trades never get above +$0.20 after entry -- the single largest loss bucket (-$5,355 over 14
  windows). Only a better entry can fix it.

## 1. Where the system stands (live config after PR #76)

STF on M1 (MACD vote), DST-correct session filter (blocks 08-18 UTC), 0.1-pip spread gate
(`MAX_SPREAD_POINTS = 1.5`), **wait up to 15 s for a zero-spread tick** (`USE_SPREAD_WAIT_ENTRY`),
30-tick breakeven arming, $5 pre-BE soft SL, **$1 post-BE cap**, **$2 staircase**.

History of the per-trade result on comparable unseen windows:

| Change | Pooled $/trade | Evidence |
|---|---|---|
| 1.0-pip gate (before) | -0.520 | Test O, 12 windows |
| + 0.1-pip gate | -0.145 | Test N 12/12; Test O comparator |
| + 15 s zero-spread wait (live now) | **-0.050** | Test O 12/12 |

## 2. Where the money goes now: trade phases

Live config, 14 windows (Test O W31-W42, Jul 2023 - Jun 2024, plus W1/W2, Jul-Sep 2026), 18,744
trades, -$825 (-$0.044/trade). Phase = how far the trade got before it exited.

| Phase | Trades | Share | Avg exit | Total | Avg peak | Median ticks held |
|---|---|---|---|---|---|---|
| 1. Pre-breakeven (timeout / $5 SL) | 0 | 0% | -- | $0 | -- | -- |
| 2a. Post-BE, never above +$0.20 | 4,573 | 24.4% | -$1.17 | **-$5,355** | $0.03 | 11 |
| 2b. Post-BE, peak $0.20-1 | 5,005 | 26.7% | -$1.19 | **-$5,951** | $0.50 | 38 |
| 2c. Post-BE, peak $1-2 (below first tier) | 2,883 | 15.4% | -$1.19 | **-$3,444** | $1.47 | 106 |
| 3a. Staircase $2 tier (peak $2-4) | 5,490 | 29.3% | +$1.92 | +$10,520 | $2.46 | 80 |
| 3b. Staircase $4 tier (peak $4-6) | 641 | 3.4% | +$3.87 | +$2,480 | $4.52 | 72 |
| 3c. Staircase $6+ tier | 152 | 0.8% | +$6.09 | +$926 | $7.05 | 80 |
| **Cap zone (2a-2c)** | 12,461 | 66.5% | -$1.18 | **-$14,751** | | |
| **Staircase zone (3a-3c)** | 6,283 | 33.5% | +$2.22 | **+$13,925** | | |

Exit P&L is almost binary: 52.9% of exits land between -$1.00 and -$1.20, 29.2% between +$1.00 and
+$2.00 (the $2 tier, filled just under it), and only 0.1% anywhere between -$1 and +$1. Both levels
overshoot: stops fire on the first tick past the level, so the cap averages -$1.18/-$1.19 and the $2
tier +$1.92; about 10% of cap exits are worse than -$1.40.

The phase shares are stable across windows (roughly 25 / 27 / 15% in the cap zone, ~30% at the $2
tier). The two wide-spread months (W34, W38) are the exceptions, with 45-69% never moving.

### The break-even line

With a binary exit, expectancy is `p * 2.216 - (1 - p) * 1.184`, where `p` is the share of trades
that reach the staircase. Break-even is **p = 34.8%**; the live config sits at **p = 33.5%**
(-$0.044/trade). Moving 1.3% of entries from "never follows through" to "reaches $2" is enough to
break even; every point beyond that is about +$0.034/trade.

## 3. Exit tests on the live config (2026-09-26)

Two windows, W1 (2026-08-25..09-22) and W2 (07-28..08-25) -- the most recent, closest to today's
tight-spread feed. All arms: production code, live config (STF, session filter on, 15 s spread wait,
0.15-pip gate), only the named exit setting changed. Exploratory: both windows had been used before.

| Window | Arm | Trades | Total | $/trade | Win % | Cap exits (avg) | Staircase exits (avg) | Reached $2+ |
|---|---|---|---|---|---|---|---|---|
| W1 | **Live: $1 cap, tiers $2/$4** | 3,716 | **-$69.60** | **-0.019** | 34.4 | 2,439 (-$1.14) | 1,277 (+$2.12) | 1,277 |
| W1 | Cap at breakeven ($0) | 3,716 | -$382.40 | -0.103 | 5.1 | 3,528 (-$0.22) | 188 (+$2.14) | -- |
| W1 | $0 cap + lock at +$0.20 | 3,716 | -$412.80 | -0.111 | 8.8 | 3,390 (-$0.22) | 326 (+$1.05) | -- |
| W1 | $1 cap + lock at +$1, tiers $1/$2/$3 | 3,716 | -$194.40 | -0.052 | 50.3 | 1,845 (-$1.14) | 1,871 (+$1.02) | 193 |
| W1 | $1 cap + lock at +$1, tiers $1/$3/$5 | 3,716 | -$179.20 | -0.048 | 50.3 | 1,845 (-$1.14) | 1,871 (+$1.03) | 193 |
| W2 | **Live** | 3,620 | **-$182.80** | **-0.050** | 34.0 | 2,388 (-$1.13) | 1,232 (+$2.05) | 1,234 |
| W2 | Cap at breakeven ($0) | 3,620 | -$329.80 | -0.091 | 6.0 | 3,402 (-$0.23) | 218 (+$2.07) | -- |
| W2 | $0 cap + lock at +$0.20 | 3,620 | -$332.60 | -0.092 | 12.1 | 3,182 (-$0.23) | 438 (+$0.91) | -- |
| W2 | $1 cap + lock at +$1, tiers $1/$2/$3 | 3,620 | -$203.80 | -0.056 | 50.7 | 1,785 (-$1.13) | 1,835 (+$0.98) | 187 |
| W2 | $1 cap + lock at +$1, tiers $1/$3/$5 | 3,620 | -$214.40 | -0.059 | 50.7 | 1,786 (-$1.13) | 1,834 (+$0.98) | 187 |

**Cap at breakeven.** Every trade now enters at zero spread, and the spread reopening by one point on
the next tick shows -$0.20 without price moving. With a $0 cap, 94-95% of trades close within ~16
ticks at -$0.22, and staircase exits collapse from ~1,250 to ~200 per window. Each loss is 5x
smaller; the lost winners cost more. (The earlier `be-floor-trail-test.md` reached the same verdict
on the pre-wait entry.)

**Lock at +$0.20.** Most trades go negative before ever reaching +$0.40, so the $0 cap underneath
still takes 88-91% of them; the lock itself only turns $2+ winners into ~$1 exits.

**Lock at +$1 (cap unchanged).** It does fix phase 2c: ~600 trades per window that used to fall from
$1-2 to the cap now exit at ~+$1, win rate 34% -> 50%, cap losses -$668 (W1). But trades that pass
+$1 usually dip back below it before running to $2+, and the lock closes them first: trades reaching
$2+ fall from 1,277 to 193, staircase profit -$793. Net worse. Tier layout above $1 barely matters.

**Conclusion.** The ~-$1.18 cap losses and the $1-2 giveback are the price of letting winners
develop; trades need room to get past the first few ticks of spread flicker and the pullback before a
$2 run. With the current entry, no exit tightening tried improves on $1 cap / $2 staircase. Untested
and plausibly marginal: a cap between $0.40 and $0.80, or above $1.

Side result: this work exposed that `ExitTradeConfig` silently turned a configured 0 into the default
(a $0 cap became $5). Fixed in PR #77; it never affected live (cap $1).

## 4. What we know about signal quality and entry timing

From the tests of 2026-09-25/26 (22 windows, 103k trades, exploratory unless noted) and earlier work:

1. **The signal's direction is real but small.** At the exact same entry tick, the signal's side
   reached breakeven within 30 ticks more often than the opposite side in **21 of 21 windows**
   (29.5% vs 33.8% failing). Earlier: +$0.17/trade directional edge, 9/9 windows. The signal is not
   random -- it just does not follow through far enough, often enough.
2. **Indicator values at entry carry no information about the outcome.** RSI and MACD values: AUC
   0.50 for "fails to reach breakeven", and they add nothing to a model once spread and tick volatility
   are known. Whatever makes a good entry is not the size of the indicator reading.
3. **Entering after price already moved your way is worse.** Price moving in the trade's direction
   over the last 5 ticks, 60 s or 5 min before entry means more failures (same direction in 11-20 of
   15-20 windows, AUC ~0.53-0.55). Small but consistent: the entry is chasing, then pays for the
   pullback.
4. **Quiet markets fail more.** More flat ticks and lower tick-level volatility before entry mean
   fewer trades get going (AUC ~0.56 at a fixed spread). Busy markets help mainly by moving price
   further per tick.
5. **Spread was the dominant cost, and it is now handled.** It explained most of the old failures
   (AUC 0.75); the 15 s wait removes it. What is left is the entry's own follow-through.
6. **Session hours mostly reflected spread.** Trading 08-18 UTC instead of 19-07 won 10/10 (Test M)
   on the old entry, but once spread is controlled directly the difference is inconclusive (per trade
   better in 6 of 8 comparable windows, total better in 4 of 8). Not shipped.
7. **Higher entry timeframe is untested properly.** STF on M5 runs through production code
   (`Config.TF_ENTRY`; ~1 min/window), but with M1-scale exits its exit mix was identical to M1 on W1.
   Earlier: M5 moves 6.0x its cost vs 2.7x on M1.
8. **Older findings still standing**: the 3-indicator vote was structurally single-indicator; MTF
   gating cost 30x the sample and picked worse trades; RSI/Bollinger/Stochastic entries all landed
   within a cent of each other; 6 regime variables and 35 cross-asset features showed no stable link
   to profit (max |corr| 0.13, sign flips).

## 5. Focus: signal quality and when to enter

**Target metric.** Judge any entry change by the share of trades that reach the staircase (phase 3)
and the share that never get above +$0.20 (phase 2a) -- not just $/trade. Break-even needs phase 3
>= 34.8% (now 33.5%) with the exits unchanged. Both shares come straight from the per-trade CSV
(`post_be_peak_profit`, `outcome`).

**Cheap screen before any full backtest.** The same-tick opposite-direction check (section 4.1)
measures an entry's short-term directional edge from ticks alone, in seconds per window. Use it to
rank candidate entries before spending full-lifecycle runs on them.

Candidate hypotheses, roughly in order of evidence:

| # | Hypothesis | Why | How to test |
|---|---|---|---|
| E1 | **Don't chase**: skip, or keep waiting within the 15 s window, when the last N ticks already moved in the trade's direction; enter on a zero-spread tick that is not above the signal price | Finding 4.3; the wait window already exists, so this is a condition inside `SpreadWaitEntryStrategy`, not a new mechanism | Add a condition to the wrapper, drive it from the backtest as today |
| E2 | **Skip dead markets**: require a minimum tick activity/volatility before entry | Finding 4.4 | Filter on pre-entry tick count or flat-tick share |
| E3 | **Measure the edge directly** under the live exits: invert the signal (`--invert-signal`) on the live config | Sizes how much of -$0.044 is direction vs structure | One run per window, no code |
| E4 | **Higher entry timeframe (M5)** with exits scaled to M5 moves | Finding 4.7 | Needs exit parameters per timeframe; two changes at once, so pre-register carefully |
| E5 | **New entry signals** ranked by the cheap screen and by phase-3 share | Findings 4.1-4.2: indicator magnitude is not the lever; timing and follow-through are | Screen first, then full lifecycle |

Not worth revisiting on current evidence: tighter post-BE caps or early locks (section 3), a dynamic
spread gate (already tested and failed), regime / cross-asset filters, n-tick confirmation.

## 6. Data and protocol for the next tests

- **Unseen windows left**: W25-W30 (2024-06-04 .. 2024-11-19) and everything before 2023-07 (tick
  history verified back to at least 2023-01, ~6 more four-week windows). W1-W24 and W31-W42 are spent.
- **Protocol** (as in Tests M/N/O): explore on spent windows, write the hypothesis and pass bar into
  `pre-registrations-2026-09-22.md`, then run unseen windows in stages 4 -> 2 -> 4 -> 2, stopping on a
  failed stage. The variant must beat the live config, not a bare baseline.
- **Run on production code**: toggle features through Config or backtest flags that drive the real
  objects (`--spread-wait`, `--exit-staircase`, `--post-be-loss-cap`, Config overrides before import).
  No script-side reimplementations.
- **Tooling**: a filter-on M1 window takes ~10 min, an M5 window ~1 min; two concurrent MT5 streams
  at most. One ungated run per window can grade several gate thresholds via `entry_spread_pips`.

## 7. Open items

- PR #77 (the 0-to-default fix) is open.
- The live orchestrator path for the spread wait (`waiting_for_spread` -> `spread_wait_confirmed` ->
  order) has not been exercised in the running app yet; PR #76 merged without that demo check.
- Demo-account spreads: on a real account zero-spread ticks may be rare, which would change the
  picture from section 1 onward.

## Artifacts

`backtest_results/EURUSD_full_lifecycle_<stamp>.csv`:

| Run | W1 | W2 |
|---|---|---|
| Live | 20260925_235017 | 20260925_235242 |
| Cap at breakeven | 20260926_005139 | 20260926_005415 |
| $0 cap + lock +$0.20 | 20260926_013156 | 20260926_013302 |
| Lock +$1, tiers $1/$2/$3 | 20260926_112410 | 20260926_112514 |
| Lock +$1, tiers $1/$3/$5 | 20260926_115206 | 20260926_115305 |

Phase breakdown: the Test O WAIT runs listed in [spread-wait-entry.md](spread-wait-entry.md) plus
the two "Live" runs above.
