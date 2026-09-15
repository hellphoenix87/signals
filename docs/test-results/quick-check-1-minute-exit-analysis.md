# Quick-Check: 1-Minute Exit Analysis (vs. Random Baseline)

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD
- **Window**: 4 weeks of M1 history, same live `MetaQuotes-Demo` source as the other reports in this category.
- **Motivation**: the fixed target/stop simulation used in the other reports assumes a hold time (up to 300 bars) this app doesn't actually commit to — the real exit strategy (`app/exit_strategies/`) is tick-driven and can close a position within seconds. That simulation is still the right tool once the exit strategy is finalized (see `docs/plans/done/mtf-backtest-validation.md`'s "out of scope"), but it doesn't answer "is this entry any good on the kind of fast hold this app would actually give it."
- **New tooling**: `scripts/backtest_signals.py --quick-check` (with `--horizon-bars`, default 1) — for each signal, looks only at the next N M1 candles and reports the best price seen in the signal's favor, the worst seen against it, and the net move at the close of the window (`evaluate_immediate_move`), independent of any target/stop distance.
- **Strategies under test**: single-timeframe (MACD+SMA+RSI, current default) and multi-timeframe (SMA/M15, RSI/M5, MACD/M1), both at `--horizon-bars 1` (one M1 minute).
- **Random-entry baseline** (ad hoc, not part of the CLI tool): coin-flip buy/sell direction on **every** M1 candle in the same 4-week window (not just candles where the real strategies fired a signal), same `evaluate_immediate_move` function, `random.seed(42)`, n=28,599. Included specifically to judge whether "had a favorable moment in the next minute" is a meaningful signal-quality metric at all, or just what any 1-minute EURUSD window looks like regardless of entry choice.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --quick-check --horizon-bars 1
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --quick-check --horizon-bars 1
  ```
  (Random baseline run via a one-off script reusing `fetch_history`/`evaluate_immediate_move` from `scripts/backtest_signals.py`, not saved as a standalone script.)
- **Spread context**: the backtest itself doesn't model spread (same as the other reports), but historical per-bar spread was pulled separately from MT5's raw `spread` field (not currently extracted by `MarketData`) for the same 4-week window: **median 0 points, mean 0.16 points (~0.016 pips), max 83 points (~8.3 pips)**. This demo feed's spread is unrealistically tight for real trading (typical retail EURUSD spreads run 0.5-2 pips even on raw/ECN accounts) — relevant context for interpreting the pip-sized moves below, and a flag that this account's costs aren't representative of live trading conditions.

## Results

| Source | n | Had win opportunity | Never positive | Still positive at end | Avg favorable | Avg adverse | Avg end |
|---|---|---|---|---|---|---|---|
| Random baseline (every candle) | 28,599 | 84.6% | 15.4% | 44.2% | +0.50 pips | +0.51 pips | -0.01 pips |
| Single-timeframe (MACD+SMA+RSI) | 5,656 | 85.3% | 14.7% | 43.0% | +0.60 pips | +0.63 pips | -0.04 pips |
| Multi-timeframe | 846 | 87.7% | 12.3% | 47.3% | +0.64 pips | +0.62 pips | -0.02 pips |

## Findings

1. **Statistically indistinguishable from random.** All three rows land in the same range on every metric: ~85-88% "had a favorable moment" (including the random baseline), favorable and adverse magnitudes nearly equal within each row (no skew toward the predicted direction), and average net move after exactly one minute ~0 pips in all three cases.
2. **"Had a winning exit opportunity" alone is not a useful signal-quality metric** — it's just what ordinary EURUSD noise looks like over any 1-minute window, real signal or coin flip. The metric that actually distinguishes skill from noise is the average end-of-window move, and it's ~0 for every source tested here.
3. **Neither the single-timeframe nor the multi-timeframe strategy shows a detectable directional edge at a 1-minute horizon.** This doesn't contradict the target/stop comparison in [multi-timeframe-target-stop-sweep.md](multi-timeframe-target-stop-sweep.md) (MTF still meaningfully outperforms single-timeframe there) — it means a fast, ~1-minute exit isn't where that edge shows up, if it exists. The edge (if real) needs more time to play out than one M1 bar; whether it needs 2 minutes, 10, or something closer to the untested 10-15 pip target flagged in the other report is still open.
4. **Follow-up, not yet done**: re-run `--quick-check` at a few longer horizons (e.g. `--horizon-bars 3/5/10`) to find whether/where a real edge starts to separate from the random baseline, before concluding there's no edge at all.
