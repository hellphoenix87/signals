# Quick-Check: Multi-Horizon Sweep

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history
- **Motivation**: [quick-check-1-minute-exit-analysis.md](quick-check-1-minute-exit-analysis.md) found no detectable edge at a 1-minute horizon for either strategy, indistinguishable from a random baseline, and flagged "does edge appear at a longer horizon" as an open follow-up. This sweeps `--horizon-bars` across 2/5/10/15 minutes for both strategies plus the same random-entry baseline methodology, to see whether/where a real edge separates from noise as hold time increases.
- **Random baseline**: same approach as the 1-minute report (coin-flip direction on every M1 candle, `random.seed(42)`, same 4-week window), re-run at each horizon via a one-off script reusing `fetch_history`/`evaluate_immediate_move`.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --quick-check --horizon-bars <2|5|10|15>
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --quick-check --horizon-bars <2|5|10|15>
  ```
  (Random baseline via the same one-off script as the 1-minute report, extended to loop over horizons.)

## Results

| Source | Horizon (min) | Had win opportunity | Avg favorable | Avg adverse | Avg end |
|---|---|---|---|---|---|
| Random | 1 | 84.6% | +0.50 | +0.51 | -0.01 |
| Random | 2 | 89.1% | +0.74 | +0.74 | **+0.00** |
| Random | 5 | 93.5% | +1.20 | +1.19 | **+0.00** |
| Random | 10 | 95.7% | +1.71 | +1.71 | -0.00 |
| Random | 15 | 96.6% | +2.10 | +2.10 | -0.00 |
| Single-tf | 1 | 85.3% | +0.60 | +0.63 | -0.04 |
| Single-tf | 2 | 89.7% | +0.85 | +0.93 | -0.09 |
| Single-tf | 5 | 93.6% | +1.37 | +1.50 | -0.14 |
| Single-tf | 10 | 95.4% | +1.88 | +2.11 | -0.23 |
| Single-tf | 15 | 96.2% | +2.29 | +2.58 | -0.26 |
| MTF | 1 | 87.7% | +0.64 | +0.62 | -0.02 |
| MTF | 2 | 91.7% | +0.92 | +0.93 | -0.04 |
| MTF | 5 | 95.3% | +1.47 | +1.51 | -0.12 |
| MTF | 10 | 96.8% | +2.01 | +2.07 | -0.12 |
| MTF | 15 | 97.2% | +2.48 | +2.57 | -0.20 |

## Findings

1. **Random baseline's avg end-of-window move stays essentially flat (~0.00 pips) at every horizon** — exactly what a coin flip should do: favorable and adverse magnitudes grow together with the square root of time (volatility scaling), but with zero net drift.
2. **Both real strategies instead show a small, consistently *negative* avg-end move that grows with horizon**: single-timeframe from -0.04 (1 min) to -0.26 (15 min); MTF from -0.02 to -0.20. Neither strategy's average outcome is ever positive at any horizon tested, and the gap below zero widens the longer you passively hold.
3. **This means a naive "enter on signal, hold N minutes, then close" approach would underperform a coin flip** for both strategies, not just fail to show an edge. The likely explanation: these signals tend to fire after a move is already partway along (both MACD's own trigger and RSI's overbought/oversold reads are inherently lagging/reactive), so a passive hold sees mild reversion rather than continuation.
4. **This doesn't mean the signals are worthless for this app specifically** — the app's real exit strategy is active and tick-driven (`app/exit_strategies/`), not a passive N-minute hold. But it does mean the entry timing itself has a slight *adverse* bias under passive hold at every horizon tested (1-15 minutes), reinforcing that active, fast exit management matters more here than for a strategy with genuine directional drift.
5. **Consistent with the target/stop findings** ([target-stop-realistic-resweep.md](target-stop-realistic-resweep.md)): win rate there improves as target/stop tightens, which is compatible with "no average drift, but real volatility to capture with a well-placed tight target" rather than "genuine directional edge" — the improvement likely comes from asking for less (a smaller favorable move within the noise band) rather than the signals getting directionally better.
