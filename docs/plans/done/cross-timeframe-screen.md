# Cross-timeframe smoke screen: which timeframe (M5-H1) is eligible?

Status: done 2026-09-26 -- 0/20 eligible (branch `cross-timeframe-screen`, off master; written 2026-09-26)

## Goal

At M1/M5, costs are about as large as any edge, so every system lands between slightly negative and
slightly positive. A 0.25-pip cost is 10% of a typical M5 bar, 5.7% of an M15 bar and 2.8% of an H1 bar.
This screen checks which timeframe, if any, is **eligible** for a real build. The yardstick is the user's:
profit pace ($/week) against drawdown (in weeks of profit), not win rate.

**Smoke screen on candles, not production code.** An eligible result earns a one-shot confirmation on
2021-2026 data and a live-code build, nothing more.

## Design (fixed before running)

- Data: MT5 M1 candles 2015-01-01 .. 2020-12-31 for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD. Never
  used for these timeframes (the spent data is 2021+ at M1/M5).
- Timeframes: M5, M10, M15, M30, H1, resampled from M1 (broker clock).
- Rules (standard parameters; entry at the close of the first bar of each signal episode):
  - Reversion: **RSI(14) fade** (< 30 buy, > 70 sell); **Bollinger(20, 2) fade** (close below/above band).
  - Momentum: **Donchian(20) breakout** (close beyond the previous 20-bar high/low); **KAMA(10,2,30)
    3-bar slope with ATR10/ATR100 >= 1.3**.
- Exits (ATR(14) of the timeframe, at entry):
  - Reversion rules: stop 1.5 ATR, target 1.5 ATR, time exit after 24 bars at the close.
  - Momentum rules: stop 1.5 ATR, then a trailing stop 1.5 ATR behind the best price; time exit after 48
    bars.
  - Played forward on M1 bars (stop checked before target inside one M1 bar = conservative). A gap
    through the stop fills at the gap's open.
- One position at a time per rule x pair x timeframe.
- Costs: a flat 0.5 pip per trade (spread + slippage, conservative for the majors).
- Sizing: 1% of $1,000 = $10 per R.
- Reported per rule x timeframe, pooled and per pair / per year: trades/week (per pair), net R/trade,
  $/week (all 6 pairs together), max drawdown in $ and in weeks of profit.
- **Eligible:** pooled net >= +0.05 R/trade, AND positive in >= 4/6 pairs, AND positive in >= 4/6 years,
  AND max drawdown (6 pairs combined) <= 26 weeks of profit. 20 rule x timeframe combinations are tested,
  so a lone marginal pass is weak evidence.

## Phases

### Phase 1: Screen script
- `scripts/cross_timeframe_screen.py`: fetch and cache M1 (backtest_results/xtf_m1_<pair>.pkl), resample,
  signals, trade simulation, report.
- Acceptance: a spot check of a few trades by hand (entry, stop, exit) matches the M1 path.

### Phase 2: Run and write up (docs/test-results/), PR (the user merges)

## QA

- Phase 1: spot check -- an M15 RSI-fade buy (2015-01-02 09:30, 1.20377, 28.5-pip risk) touches its
  target at 10:04 and never its stop in the raw M1 data, as simulated; a time exit lands exactly 24 bars
  after entry. First 100 bars per series skipped (ATR warm-up).
- Phase 2: 0/20 rule x timeframe combinations eligible. The reversion gross edge shrinks with the
  timeframe (+0.07 R at M5 -> ~0 at H1) as fast as the cost does; momentum has no gross edge up to H1.
  Report: docs/test-results/cross-timeframe-screen.md.

