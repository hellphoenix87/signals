# Bollinger Bands as MTF M1 Entry Indicator — Two-Window Signal-Quality Proxy

Date: 2026-09-21

## Setup

- **Symbol**: EURUSD, 4-week windows, MTF gate at today's live `Config` defaults (target=8/stop=5 pips, session filter on, ADX gate 20.0, score threshold 0.6) — same config as [mtf-pullback-gate-direction-fix.md](mtf-pullback-gate-direction-fix.md), whose fixed pullback gate is the current production gate.
- **Indicator**: `generate_bollinger_signal` (`app/signals/indicators/bollinger.py`, window=20, std_mult=2.0) — `buy` when the latest M1 close breaches the lower band (oversold, mean-reversion), `sell` when it breaches the upper band (overbought).
- **Windows**: window 1 = `--start-pos 1` (most recent 4 weeks), window 2 = `--start-pos 28801` (immediately preceding, non-overlapping 4 weeks) — the same two windows used throughout the PR #62 investigation.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --start-pos 1 --mtf --mtf-entry-indicator bollinger
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --start-pos 28801 --mtf --mtf-entry-indicator bollinger
  ```
- **Wiring sanity check**: the raw indicator, run single-timeframe with no MTF gate (`--indicators bollinger`, window 1), fires constantly — 468 signals in one week alone (267 buy/201 sell), with real win/loss resolution (36.0% win rate). This confirms the 0-signal MTF results below come from the gate, not from the indicator itself never firing or a wiring bug.

## Results

| Window | Signals (buy/sell) | Win rate |
|---|---|---|
| Window 1 (`--start-pos 1`) | **0** | n/a |
| Window 2 (`--start-pos 28801`) | **0** | n/a |

## Findings

1. **Bollinger Bands produces zero MTF-gated signals in both independent windows.** Not a fluke of one month — the raw indicator is highly active (see wiring check above), so this is the pullback gate rejecting every single Bollinger-triggered candidate across 8 weeks of data.
2. **Same structural pattern as RSI** ([mtf-pullback-gate-direction-fix.md](mtf-pullback-gate-direction-fix.md) finding 2: "RSI is now consistently, robustly unusable — zero signals in both independent windows"). Both are contrarian/mean-reversion triggers: they fire *while* price is still moving against the eventual direction (buy while price is still falling into the lower band, sell while still rising into the upper band). The MTF pullback gate (`MultiTimeframeStrongSignalStrategy._pullback_completed`) specifically demands the mirror of that shape — price already reversed and closed back through its M1 SMA20 — which a same-bar contrarian trigger structurally can't satisfy. SMA's bullish/bearish crossover (which fires *after* the reversal) and MACD's trend-following momentum look nothing like this failure mode, which is presumably why they're the two indicators that do produce signals under this gate.

## Verdict: **ruled out — fails the 10-signals-per-window bar in both windows (0/0)**

Consistent with RSI's exclusion from this plan's scope. Per the plan's gating rule, Subphase 3.2 (entry-excursion trace) and Phase 5 (full-lifecycle P&L) are **skipped for Bollinger** — there are no MTF-gated signals to trace or trade in either window, so a further trace would have nothing to measure.
