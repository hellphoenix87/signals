# Cross-timeframe smoke screen (M5-H1): nothing is eligible

Date: 2026-09-26. Plan: [`docs/plans/done/cross-timeframe-screen.md`](../plans/done/cross-timeframe-screen.md).
Script: `scripts/cross_timeframe_screen.py`. **Smoke screen on candles, not production code.**

## Setup (pre-registered)

- 2015-2020 M1 candles for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF and USDCAD, resampled to M5, M10, M15,
  M30 and H1. This data had not been used before at these timeframes.
- Four rules, entering on the first bar of each signal: RSI(14) fade, Bollinger(20, 2) fade, Donchian(20)
  breakout, and KAMA slope + ATR10/ATR100 >= 1.3.
- Exits sized on ATR(14) of the timeframe, played forward on M1 bars:
  - fades: stop and target at 1.5 ATR, time exit after 24 bars;
  - momentum rules: stop at 1.5 ATR, then a trailing stop 1.5 ATR behind the best price, time exit after
    48 bars.
- One position at a time per rule/pair/timeframe; cost 0.5 pip per trade; $10 per R.
- Eligible if: net >= +0.05 R/trade, positive in >= 4/6 pairs and >= 4/6 years, and a max drawdown of
  no more than 26 weeks of profit.

## Result: 0 of 20 combinations eligible

| rule | M5 | M10 | M15 | M30 | H1 |
|---|---|---|---|---|---|
| RSI fade, net R/trade | -0.016 | -0.014 | -0.009 | -0.030 | -0.025 |
| Bollinger fade | -0.032 | -0.009 | -0.017 | -0.024 | -0.031 |
| Donchian breakout | -0.165 | -0.135 | -0.110 | -0.074 | -0.044 |
| KAMA + vol exp | -0.078 | -0.059 | -0.042 | -0.050 | +0.001 |

Every $/week figure is negative except KAMA + vol exp on H1 (+$0.08/week for 6 pairs, 3/6 pairs and 3/6
years positive, drawdown of about 10,000 weeks of profit).

## Why: the edge shrinks with the timeframe as fast as the cost does

Gross R/trade (before costs) and the cost in R:

| rule | | M5 | M10 | M15 | M30 | H1 |
|---|---|---|---|---|---|---|
| RSI fade | gross | **+0.076** | +0.047 | +0.039 | +0.002 | -0.002 |
| Bollinger fade | gross | **+0.068** | +0.058 | +0.035 | +0.010 | -0.008 |
| Donchian breakout | gross | -0.067 | -0.070 | -0.059 | -0.039 | -0.021 |
| KAMA + vol exp | gross | -0.014 | -0.016 | -0.007 | -0.026 | +0.018 (se 0.018) |
| cost at 0.5 pip | | 0.09-0.10 | 0.06-0.07 | 0.05 | 0.03 | 0.02 |

- **The mean-reversion edge is a short-horizon effect.** It is clearly real at M5 (+0.07 R, about 20
  standard errors), about half as large at M15, and gone by M30/H1. It shrinks about as fast as the cost
  does, so moving to a slower timeframe does not leave more of it behind. The "sweet spot" idea from the
  earlier discussion (constant edge, falling cost) is **not** what the data shows.
- **Momentum has no gross edge on any timeframe up to H1.** Donchian breakouts lose even before costs.
  KAMA + vol exp is flat, apart from an H1 value of +0.018 R, which is inside its own noise (one
  standard error).
- **Cost decides the fades.** At 0.2 pip instead of 0.5 (info only -- not the pre-registered bar), the RSI
  fade nets +0.039 R at M5 and +0.020 at M15. That is the same thin band as the live-code M5 RSI-fade
  test (+0.075 R with zero-spread entries). The edge exists, but only at the short end, where costs bite
  hardest.

## What this means

- For these four standard rules on the six majors, going from M5 to H1 does not make an eligible system.
  The per-trade edge falls as fast as the cost.
- The only positive gross edge found is short-horizon mean reversion, and its value depends almost
  entirely on the execution cost (zero-spread entries, slippage) -- the same conclusion as at M1/M5.
- Not covered by this screen: daily and slower timeframes (where FX trend-following is documented),
  session-structure rules (opening-range breakouts), other exit shapes, and crosses/metals.
