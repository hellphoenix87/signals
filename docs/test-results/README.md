# Test Results

One report per backtest run of `scripts/backtest_signals.py`, each with a **Setup** section (exact command, symbol/window, strategy configuration, evaluation method) and a **Findings** section (what the numbers mean, not just what they are). Raw per-signal CSVs referenced by these reports live under `backtest_results/` (gitignored, local only) — reports summarize and interpret them, not replace them.

All runs so far were made against real MT5 historical data (`MetaQuotes-Demo`, EURUSD) via `pipenv run python scripts/backtest_signals.py`, no real orders placed.

| Report | Strategy | Evaluation | Date |
|---|---|---|---|
| [single-timeframe-macd-only-baseline.md](single-timeframe-macd-only-baseline.md) | Single-timeframe, MACD only | Fixed target/stop, stop sweep | 2026-09-13 |
| [single-timeframe-macd-sma-rsi.md](single-timeframe-macd-sma-rsi.md) | Single-timeframe, MACD+SMA+RSI | Fixed target/stop | 2026-09-14 |
| [multi-timeframe-target-stop-sweep.md](multi-timeframe-target-stop-sweep.md) | Multi-timeframe (SMA/M15, RSI/M5, MACD/M1) | Fixed target/stop, stop sweep | 2026-09-14 |
| [quick-check-1-minute-exit-analysis.md](quick-check-1-minute-exit-analysis.md) | Single-timeframe + multi-timeframe + random baseline | `--quick-check`, 1-minute horizon | 2026-09-14 |
| [target-stop-realistic-resweep.md](target-stop-realistic-resweep.md) | Single-timeframe + multi-timeframe | Fixed target/stop, 15:5 and 10:5 | 2026-09-14 |
| [single-indicator-ablation.md](single-indicator-ablation.md) | MACD-only, SMA-only, RSI-only (isolated) | Fixed target/stop | 2026-09-14 |
| [macd-crossover-fix.md](macd-crossover-fix.md) | MACD crossover variant (solo + combined) | Fixed target/stop | 2026-09-14 |
| [mtf-gate-sensitivity-sweep.md](mtf-gate-sensitivity-sweep.md) | Multi-timeframe, `MTF_SCORE_THRESHOLD`/`MTF_ADX_MIN_STRENGTH` overrides | Fixed target/stop | 2026-09-14 |
| [quick-check-multi-horizon.md](quick-check-multi-horizon.md) | Single-timeframe + multi-timeframe + random baseline | `--quick-check`, 2/5/10/15-minute horizons | 2026-09-14 |
| [production-fix-validation.md](production-fix-validation.md) | Single-timeframe + multi-timeframe, post MACD-crossover + RSI-weighting fix | Fixed target/stop, 15:5/10:5/**7:5** | 2026-09-14 |
| [mtf-adx-finer-sweep.md](mtf-adx-finer-sweep.md) | Multi-timeframe, post-fix, `MTF_ADX_MIN_STRENGTH` 24/26/28/30 | Fixed target/stop | 2026-09-14 |
| [spread-and-second-window-validation.md](spread-and-second-window-validation.md) | Single-timeframe + multi-timeframe | `--spread-pips`/`--start-pos`, 7:5 and 10:5, two windows | 2026-09-14 |
| [ratio-sweet-spot-search.md](ratio-sweet-spot-search.md) | Single-timeframe + multi-timeframe, spread-adjusted | Fixed target/stop, target=3-10 sweep at stop=5 | 2026-09-14 |

**Headline result**: [ratio-sweet-spot-search.md](ratio-sweet-spot-search.md) — with a realistic 1-pip spread modeled, the win-rate-vs-breakeven gap **plateaus around -7 points for single-timeframe across target=3-5 pips (stop=5)** and doesn't close at any ratio tested (3 through 10 pips). This looks like a structural floor for this signal engine + spread combination, not a matter of finding the right ratio — further target/stop tuning has diminishing returns as a lever.

Related plan: [`docs/plans/done/mtf-backtest-validation.md`](../plans/done/mtf-backtest-validation.md).
