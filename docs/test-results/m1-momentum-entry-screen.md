# M1 momentum entry screen: trigger + strength filter + session hours

Date: 2026-09-26. Plan: [`docs/plans/done/m1-momentum-entry.md`](../plans/done/m1-momentum-entry.md).
Verdict: **nothing passes. M1 momentum built from simple indicators does not beat a random direction by
enough to matter in this data. Phase 4 (pre-registration, confirmation, production build) was not started.**

## Setup

- Tool: `scripts/signal_lab.py` (restored from closed PR #79). Each M1 candle close in the chosen hours gets
  the live entry: the first tick within 15 s whose spread is <= `SPREAD_WAIT_MAX_POINTS`. From that tick,
  the barrier game (+1 pip staircase before -0.5 pip cap) is played in both directions. A rule is scored
  by its **edge**: its win % minus the same-tick random-direction win %. This matches the full backtest on
  95.8% of live trades.
- Windows: W1, W2, W31-W42 (28 days each, all **spent**), so these results are exploratory.
- Hours: `live` = 19-07 UTC (what the live session filter allows), `ldn_ny` = 08-18 UTC (what it
  blocks), `all`. The conversion to UTC is the production one, which handles DST.
- Commands:
  `PYTHONPATH=. pipenv run python scripts/signal_lab.py [--cached] --momentum --hours=live|ldn_ny|all`,
  and `--macd` for the production MACD baseline (computed per candle).
- Indicators (standard parameters, fixed before running): Keltner EMA20 +/- 2 x ATR10 breakout,
  Supertrend(10,3) flip, KAMA(10,2,30) 3-bar slope, Hull(21) slope, Aroon(25) cross, ROC(10) beyond +/-1
  std(100). Filters: ADX(14) rising over 3 bars and >= 20, efficiency ratio(10) >= 0.3, ATR10/ATR100 >= 1.3.
  I checked them on a synthetic up / flat / down series: each points the right way, and there is no
  lookahead (the value at candle i is the same whether or not the series is cut at i).
- Bar (from the plan): pooled edge >= +3 pp, positive in >= 10/12 of the windows with >= 100 trades,
  better than MACD in the same hours, and better than the simpler layer.

## Baselines

| hours | random win % | production MACD edge | MACD win % |
|---|---|---|---|
| live | 32.14 | **+1.33 pp** (8/12) -- matches the recorded live +1.32 | 33.38 |
| ldn_ny | 33.00 | **-0.16 pp** (6/13) | 32.61 |
| all | 32.69 | -- | -- |

Break-even with live fills: 34.8% win rate.

## Results (edge pp, windows positive)

### Live hours (19-07 UTC): every trigger is negative

| trigger | alone | + ADX rising | + ER >= 0.3 | + vol exp |
|---|---|---|---|---|
| Keltner breakout | -1.14 (5/11) | -1.35 | -0.92 | -1.56 |
| Supertrend flip | -0.09 (2/8) | -1.67 (15 tr/win) | -0.31 | +1.00 (43 tr/win) |
| KAMA slope | -0.51 (4/14) | -1.08 | -0.75 | -0.53 |
| Hull slope | -0.62 (4/14) | -1.03 | -0.75 | -0.81 |
| Aroon cross | -0.02 (5/8) | -3.79 (35 tr/win) | -1.17 | +0.58 (43 tr/win) |
| ROC break | -1.06 (5/12) | -1.36 | -1.02 | -0.68 |

The two positive cells come from about 43 trades per window and have no windows with >= 100 trades,
so they are noise. This matches the earlier finding: in these hours the market mean-reverts at M1.

### London/NY hours (08-18 UTC): near zero, with a consistent lift from volatility expansion

| trigger | alone | + ADX rising | + ER >= 0.3 | + vol exp |
|---|---|---|---|---|
| Keltner breakout | +0.08 (8/13) | +0.06 | +0.08 | +0.83 (10/13, 227 tr/win) |
| Supertrend flip | -0.19 (6/13) | +1.37 (24 tr/win) | -0.87 | +0.70 (36 tr/win) |
| KAMA slope | +0.16 (8/13) | +0.14 | +0.02 | **+1.14 (11/13, 976 tr/win)** |
| Hull slope | +0.13 (7/13) | -0.12 | +0.04 | +0.49 (9/13) |
| Aroon cross | -0.24 (6/13) | +1.62 (53 tr/win) | -0.07 | -3.29 (40 tr/win) |
| ROC break | -0.11 (8/13) | -0.15 | -0.17 | +0.64 (9/13) |

The older rules in these hours flip too: the fades turn negative (Bollinger fade -0.69, RSI fade 0.00),
and follow/trend rules turn slightly positive (Bollinger follow +0.69, 240-bar trend +0.29..+0.34).

### All hours: the two regimes cancel out

Best: KAMA slope + vol exp at +0.26 pp (10/13). Everything else is between -0.7 and +0.9 pp, and the
positive ones are the low-count cells.

## Verdict

- **No configuration passes.** The best is KAMA slope + ATR10/ATR100 >= 1.3 in London/NY hours: +1.14 pp,
  11/13 windows, ~976 trades per window. That is consistent, but it is a third of the +3 pp bar. Its
  absolute win rate is 33.81%, which is below the 34.8% break-even (about -$0.03 per trade estimated,
  against about -$0.05 for the live MACD in live hours).
- The 3.3 layer (context) is the session-hours split, and it is what decides the sign. Momentum only has a
  (small) positive edge in London/NY hours, and there only together with volatility expansion. Volatility
  expansion was the only filter that improved most triggers. ADX rising and the efficiency ratio added
  nothing or made things worse.
- A structural point for any future entry work: in London/NY hours the random-direction win rate is already
  33.0%, against 32.1% in live hours, so an entry there needs +1.8 pp to break even instead of +2.7 pp.
  That makes the hours question a bigger lever than the choice of indicator, and it concerns the session
  filter, not the signal.
- Per the plan: stop here. M1 momentum with simple indicators does not beat random by enough in this data.
