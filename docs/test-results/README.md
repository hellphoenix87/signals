# Test Results

One report per backtest run of `scripts/backtest_signals.py`, each with a **Setup** section (exact command, symbol/window, strategy configuration, evaluation method) and a **Findings** section (what the numbers mean, not just what they are). Raw per-signal CSVs referenced by these reports live under `backtest_results/` (gitignored, local only) — reports summarize and interpret them, not replace them.

All runs so far were made against real MT5 historical data (`MetaQuotes-Demo`, EURUSD) via `pipenv run python scripts/backtest_signals.py`, no real orders placed.

| Report | Strategy | Evaluation | Date |
|---|---|---|---|
| [single-timeframe-macd-only-baseline.md](single-timeframe-macd-only-baseline.md) | Single-timeframe, MACD only | Fixed target/stop, stop sweep | 2026-09-13 |
| [single-timeframe-macd-sma-rsi.md](single-timeframe-macd-sma-rsi.md) | Single-timeframe, MACD+SMA+RSI | Fixed target/stop | 2026-09-14 |
| [multi-timeframe-target-stop-sweep.md](multi-timeframe-target-stop-sweep.md) | Multi-timeframe (SMA/M15, RSI/M5, MACD/M1) | Fixed target/stop, stop sweep | 2026-09-14 |
| [quick-check-1-minute-exit-analysis.md](quick-check-1-minute-exit-analysis.md) | Single-timeframe + multi-timeframe + random baseline | `--quick-check`, 1-minute horizon | 2026-09-14 |

Related plan: [`docs/plans/done/mtf-backtest-validation.md`](../plans/done/mtf-backtest-validation.md).
