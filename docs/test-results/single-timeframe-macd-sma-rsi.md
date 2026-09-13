# Single-Timeframe (MACD+SMA+RSI) — Post-Fix Check

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD
- **Window**: 4 weeks of M1 history (~28,800 candles), same live `MetaQuotes-Demo` MT5 source as [single-timeframe-macd-only-baseline.md](single-timeframe-macd-only-baseline.md)
- **Strategy under test**: `StrongSignalStrategy` via `strategy_factory(config=Config)`, single-timeframe path, **after** activating SMA and RSI alongside MACD (`app/signals/signal_generation.py`, `app/config/settings.py::ENTRY_SMA_SHORT_WINDOW`/`ENTRY_SMA_LONG_WINDOW`/`ENTRY_RSI_PERIOD`) — `indicators = {"macd": ..., "sma": ..., "rsi": ...}`, so `confidence` now reflects real 3-way agreement instead of a single trivial vote. Full change described in [`docs/plans/done/mtf-backtest-validation.md`](../plans/done/mtf-backtest-validation.md), Phase 1.
- **Evaluation method**: fixed target/stop simulation (`evaluate_signal`), same as the baseline report — for direct comparison, only `--stop-pips 10` was re-run here (the value in the middle of the baseline's 5/10/20 sweep).
- **Command**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --stop-pips 10
  ```
  `--target-pips` left at default (`Config.DEFAULT_TP_PIPS = 50`).
- **Spread**: not modeled (same as baseline).

## Results

| Stop (pips) | Target (pips) | Signals (buy/sell) | Wins | Losses | Undecided | Win rate (decided) | Avg bars to resolution |
|---|---|---|---|---|---|---|---|
| 10 | 50 | 5,657 (2867/2790) | 28 | 2,067 | 3,562 | 1.3% | 128.1 |

For comparison, the baseline (MACD only) at the same stop=10: 16,082 signals, 1.1% win rate.

## Findings

1. **Signal count dropped 65%** (16,082 -> 5,657) — the 3-indicator vote-gating fix worked as intended, requiring genuine majority agreement instead of a single indicator's trivially-satisfied vote.
2. **Win rate barely moved** (1.1% -> 1.3%) — the fix addressed overtrading, not the underlying lack of directional edge. All three active indicators (MACD, SMA, RSI as wired here) are trend-following/momentum indicators; requiring more of them to agree filters out some noise but doesn't add genuine predictive signal if none of them individually have it at this timeframe/configuration.
3. **Conclusion carried into the next comparison**: since fixing the single-timeframe vote-gating wasn't sufficient on its own, the natural next test was the already-built but never-backtested multi-timeframe strategy (`MultiTimeframeStrongSignalStrategy`) — see [multi-timeframe-target-stop-sweep.md](multi-timeframe-target-stop-sweep.md).
