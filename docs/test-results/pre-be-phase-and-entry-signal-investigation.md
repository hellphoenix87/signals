# Pre-BE Phase, Entry Signals, and the First Properly Pre-Registered Out-of-Sample Tests

Date: 2026-09-22/23

**TL;DR**: MT5 "Max bars in chart" was raised, unlocking ~27 months of history (12 four-week
windows instead of 3.5). Every test below fixed its rule, thresholds and pass criteria *in
writing before the data was fetched*. Two changes passed: the **`$2` staircase trail**
(12 windows) and a **30-tick breakeven arming wall** (10/12 windows, +$0.066/trade, the
largest single improvement found so far). Eight other ideas failed, including every
market-state/regime rule and every entry-indicator change. **No `Config` change was made
in this plan** -- both survivors need forward testing on demo first.

## Setup

EURUSD, `scripts/backtest_exit_strategy.py --full-lifecycle`, 10,000 ticks/trade, live `$1`
post-BE cap. Windows are `--start-pos` offsets of 28,800 M1 bars (~4 weeks):
W1=1, W2=28801, W3=57601, W4=86401, W5=115201, W6=144001, W7=172801, W8=201601,
W9=230401, W10=259201, W11=288001, W12=316801 (W12 ~ Oct-Nov 2025).

**Totals below are ex-`exhausted`** -- trades still open at the tick cutoff are marked at an
arbitrary price and average +$40-55, which flatters whichever variant leaves more of them open.

## What passed

### 1. `$2` staircase trail (confirmed, 12 windows)
Beat the live 60%/`$2` percentage trail in 7 of 9 new windows and on total (-$34,291 vs
-$34,974). The two losses (W8 -$197, W9 -$386) are smaller than the baseline's exhausted
credit in those windows (+$1,036 / +$871), so like-for-like it is 9/9, plus W1-W3 from the
prior session = 12 windows. Worth ~+$0.02/trade.

### 2. 30-tick breakeven arming wall (`EXIT_BE_ARMING_TICKS` 90 -> 30) -- **PASS, 10/12**

| | baseline (90) | arm=30 |
|---|---|---|
| Total, 12 windows | -$44,122 | **-$41,550 (+$2,571)** |
| Per trade | -$1.022 | **-$0.956 (+$0.066)** |
| Windows won | -- | 10 of 12 |

Mechanism (W8): halves the expensive distance exits and makes the cheap time exit cheaper.

| bucket | baseline | arm=30 |
|---|---|---|
| `profit_drop` (pre-BE soft SL) | 340 trades, -$2,005 (-$5.90) | **170 trades, -$1,015** |
| `failed_to_reach_be` (time exit) | 1,021 @ -$2.25 | 1,691 @ **-$1.63** |
| `profit_drop_after_be` | 1,528, -$1,846 | 1,203, -$1,441 |
| trail exits | 930, +$1,734 | 752, +$1,482 |

Why it is nearly free: breakeven arms at **median 0-11 ticks** (p90 = 5-55), so only 2.5-15.1%
of all trades arm after tick 30. The wall kills non-starters 3x sooner while keeping almost
every trade that ever works.

Both losses are the two lowest-ATR windows (W1 at 0.74, W10 at 0.96, and W10 is a -$6 tie);
gains scale with volatility (+$672 at ATR 1.47, +$459 at 1.11). **Not** encoded as a
volatility-conditional rule -- that is the shape of every rule that failed below.

## What failed

| Idea | Result |
|---|---|
| Wide/disabled post-BE cap | Not reproducible: W1 gave +$1,124 / -$34 / +$567 and W3 -$595 / -$2,802 / -$138 across re-runs. Cause: 42-59 trades per window lose >$20 (to -$60) while the trail still caps winners at ~$18, so totals swing on a few tail trades. **Structurally unsound, not merely unlucky.** |
| ATR-normalized thresholds (clamp 0.5-3.0 on soft SL + cap + trail floor) | **2/12**, -$565. Scaling the `$1` post-BE cap up to `$3` cost more (-$914 on W8) than the softer SL and wider trail floor gained (+$673). |
| Longer/removed arming wall (300 ticks, disabled) | **2/4 screen**. Released trades do not reach their peak -- they bleed to the `$5` stop instead (`profit_drop` 340 -> 766, -$2,005 -> -$4,161). |
| Tighter pre-BE soft SL (`$2`) | **6/12**, -$401. Helps alone on 3/4 screen windows, stops helping once the 30-tick wall exists (overlapping mechanism). |
| arm=30 + post-BE cap `$1.5` | **0/4 vs arm=30 alone**, -$619. `$1` has now beaten all 10 other cap values tested. |
| arm=30 + pre-BE SL `$1.5` | **1/4 vs arm=30 alone**, -$382. |
| Market state / regime (6 variables) | H1 ER 5/9, D1 ER 4/7, M1 ATR 6/9 (bar was 8/9; chance ~5). 35 cross-asset features from US500/USTEC/DE40/XAUUSD/USDJPY/GBPUSD: max \|Spearman\| with realized profit **0.13**, and sign flips between windows (DE40 vol: +0.103 on W8, -0.125 on W4). |
| Entry indicator changes | See below -- no effect on per-trade expectancy. |

## The entry signal is not where the money goes

Three independent tests, same conclusion:

| Test | Variants | Per-trade result |
|---|---|---|
| STF single indicator | RSI / Bollinger / Stochastic | -$0.68 / -$0.69 / **-$0.69** |
| MTF entry indicator | MACD / SMA (RSI fires ~0 times) | -$1.44 / -$1.29 |
| MTF gating vs none | gated / ungated | **-$1.19 / -$0.88** |

- **The 3-indicator vote is structurally single-indicator.** Weights MACD 1 / SMA 1 / RSI 2.5,
  threshold 0.5, total 4.5: MACD+SMA agreeing = 0.44, below threshold; RSI alone = 0.56, above.
  MACD and SMA together **can never fire without RSI**. Measured: 98.4% of 43,186 STF trades
  are RSI-alone, 1.5% MACD+RSI, 0.1% RSI+SMA. Indicator agreement carries no edge
  (MACD+RSI -$0.94 vs RSI-alone -$0.88).
- **Bollinger/Stochastic "win" only by trading less/more.** Bollinger beats baseline on total
  in 4/4 windows while trading half as often; per trade it is -$0.69 vs -$0.68. Stochastic
  trades 2x as often for the same -$0.69.
- **MTF gating costs 30x the sample size and picks worse trades** (10 of 12 windows). Its
  smaller total loss comes from abstaining, not selecting.
- **RSI, Bollinger and Stochastic are all structurally incompatible with the MTF pullback
  gate** (1-18 signals/window, 0 in 5 of 12), which is why the live entry indicator is MACD:
  the gate rejects nearly everything else. Not because MACD is good -- a forward-move proxy
  ranks it and SMA as *anti*-predictive (hit rate 47.9% / 46.6%, negative mean forward move
  at 5/15/30/60 min) vs RSI 52.1%, Bollinger 52.8%, Stochastic 51.9%.

## Why the system loses (measured, not inferred)

| | W3 (recent) | W8 (year-old) |
|---|---|---|
| Win rate | 28.1% | 24.7% |
| Avg win / loss | $1.93 / $1.32 | $2.92 / $2.13 |
| Reward:risk | 1.47 | 1.37 |
| **Breakeven win rate needed** | **40.5%** | **42.1%** |
| Expectancy | -$0.40/trade | -$0.88/trade |

A 13-17 point win-rate gap. The signal's entire measured directional edge is **+$0.17/trade**
(normal minus inverted, positive in **9 of 9** windows, +$5,482 -- the edge is real, just small).

Cost context: median M1 move is 0.40 pips = $0.80 at 0.2 lots, against a round-trip spread of
$0.07-$0.69 (measured; 0.34 pips mean in W8's low-liquidity hours). Move-to-cost by timeframe:
M1 2.7x, M5 6.0x, M15 12.7x, M30 21.3x, H1 27.3x. Median time-to-peak is 1,826-1,902 ticks
(21-72 min), i.e. ~20x the arming wall.

Also note: `USE_SESSION_FILTER` blocks UTC 08-18 for EURUSD, so **all** results here (and the
live bot) trade only the Asian session plus the late US tail -- never the London/NY overlap.

## Config facts worth knowing

- `N_TICK_CONFIRMATION = 1` while `USE_N_TICK_CONFIRMATION = True`: the wrapper only applies
  when `n_ticks > 1`, so n-tick confirmation **reads as enabled but never engages**.
- `USE_ML_ENTRY_MODEL = False` and `ENTRY_ATR_PERIOD/MOVE_MULT = 0/0`: `MLSignalStrategy` and
  `AtrMomentumFilteredSignalStrategy` are built and wired but inert.

## Takeaways

1. **Exit-side changes are the only ones that have ever moved the number.** Every entry-side
   and state-conditioning test lands within a cent of baseline per trade.
2. **Both survivors are still only backtest evidence.** Forward-test `EXIT_BE_ARMING_TICKS = 30`
   and the staircase trail on demo before trusting either. The wide-cap result looked far more
   convincing than these and was noise.
3. **Overfitting budget spent here**: pre-BE thresholds $5/$2/$1.5; post-BE caps $1/$1.5 (plus
   9 earlier); arming walls 90/300/0/30. Any further threshold search needs fresh windows.
4. **Untested and promising**: raising the entry timeframe (M5 entry has 6.0x move-to-cost vs
   M1's 2.7x). Blocked by sample size under MTF gating (~25 trades/window), so it would need
   to run ungated.
