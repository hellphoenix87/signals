# Stochastic Oscillator as MTF M1 Entry Indicator — Two-Window Signal-Quality Proxy

Date: 2026-09-21

## Setup

- **Symbol**: EURUSD, 4-week windows, MTF gate at today's live `Config` defaults (target=8/stop=5 pips, session filter on, ADX gate 20.0, score threshold 0.6) — same config as [mtf-pullback-gate-direction-fix.md](mtf-pullback-gate-direction-fix.md), whose fixed pullback gate is the current production gate.
- **Indicator**: `generate_stochastic_signal` (`app/signals/indicators/stochastic.py`, period=14, oversold=20.0, overbought=80.0) — `buy` when `%K` drops below 20 (oversold, mean-reversion), `sell` when `%K` rises above 80 (overbought).
- **Windows**: window 1 = `--start-pos 1` (most recent 4 weeks), window 2 = `--start-pos 28801` (immediately preceding, non-overlapping 4 weeks) — the same two windows used throughout the PR #62 investigation.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --start-pos 1 --mtf --mtf-entry-indicator stochastic
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --start-pos 28801 --mtf --mtf-entry-indicator stochastic
  ```
- **Wiring sanity check**: the raw indicator, run single-timeframe with no MTF gate (`--indicators stochastic`, window 1), fires constantly — 1844 signals in one week alone (944 buy/900 sell), with real win/loss resolution (36.9% win rate). This confirms the near-zero MTF results below come from the gate, not from the indicator itself never firing or a wiring bug.

## Results

| Window | Signals (buy/sell) | Win rate (decided) |
|---|---|---|
| Window 1 (`--start-pos 1`) | 2 (0/2) | 0.0% |
| Window 2 (`--start-pos 28801`) | 8 (3/5) | 12.5% |

Breakeven at target=8/stop=5 is 5/13 ≈ 38.5% (same as [mtf-pullback-gate-direction-fix.md](mtf-pullback-gate-direction-fix.md)).

## Findings

1. **Stochastic clears zero signals but comes close** — 2 and 8 signals respectively, both far short of the plan's 10-signals-per-window bar meant to rule out an "RSI-style zero-signal structural failure." A sample this small (10 signals pooled) isn't usable for any win-rate conclusion regardless of the observed numbers.
2. **Same structural pattern as RSI and Bollinger**: Stochastic's %K is a same-bar contrarian trigger (buy while price is still falling toward the period low, sell while still rising toward the period high), the same shape the MTF pullback gate (`MultiTimeframeStrongSignalStrategy._pullback_completed`) is built to reject — see [bollinger-entry-ablation.md](bollinger-entry-ablation.md) finding 2 for the same mechanism. The handful of signals that did get through are plausible as gate false-positives (cases where a Stochastic trigger happened to coincide with a genuine SMA20 reversal by chance) rather than evidence the indicator works under this gate.
3. **The observed win rates (0.0%, 12.5%) are both far below the 38.5% breakeven**, for whatever that's worth at n=2 and n=8 — consistent with, not contradicting, the "structurally incompatible" read.

## Verdict: **ruled out — fails the 10-signals-per-window bar in both windows (2, 8)**

Consistent with RSI's and Bollinger's exclusion. Per the plan's gating rule, Subphase 4.2 (entry-excursion trace) and Phase 5 (full-lifecycle P&L) are **skipped for Stochastic** — 10 signals pooled across two independent windows is too small a sample to trace or trade meaningfully.
