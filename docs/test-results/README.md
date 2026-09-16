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
| [rsi-weight-sweep.md](rsi-weight-sweep.md) | Single-timeframe, `ENTRY_RSI_WEIGHT` 1.0-4.0 | Fixed target/stop, spread-adjusted | 2026-09-14 |
| [final-signal-quality-summary.md](final-signal-quality-summary.md) | Single-timeframe + multi-timeframe, final production config | Fixed target/stop, spread-adjusted, 3-ratio summary | 2026-09-14 |
| [session-filter-analysis.md](session-filter-analysis.md) | Multi-timeframe + single-timeframe, hour-of-day breakdown | Fixed target/stop, spread-adjusted, two windows | 2026-09-15/16 |
| [ml-entry-model-comparison.md](ml-entry-model-comparison.md) | Single-timeframe + multi-timeframe, RandomForest entry model vs hand-coded rules | Fixed target/stop, spread-adjusted, two windows | 2026-09-16 |

**Headline result**: [session-filter-analysis.md](session-filter-analysis.md) — MTF badly underperforms during 08:00-18:59 UTC (London/NY session) across two independent windows; no such effect for single-timeframe. **Combined with the validated 7:5 ratio and realistic spread, MTF + this session filter reaches 42.2% win rate pooled across both windows — right at the 41.7% breakeven**, the best result in the whole investigation. Implemented in production (`SessionFilteredSignalStrategy`, `Config.USE_SESSION_FILTER`) and verified to reproduce the finding exactly; `USE_MULTI_TIMEFRAME_SIGNALS` flipped to `True` as a result (see `docs/plans/done/session-filter-enable-mtf.md`).

**Also tried and rejected**: [ml-entry-model-comparison.md](ml-entry-model-comparison.md) — a RandomForest trained on the same indicators lost to the hand-coded rules in all 4 window/strategy combinations tested (single-tf: -1.8 to -6.3 points; MTF: -9.8 to -18.9 points). `Config.USE_ML_ENTRY_MODEL` stays `False`; the infrastructure remains for future iteration with different features/labels/models.

Related plans: [`docs/plans/done/mtf-backtest-validation.md`](../plans/done/mtf-backtest-validation.md), [`docs/plans/done/session-filter-enable-mtf.md`](../plans/done/session-filter-enable-mtf.md), [`docs/plans/done/ml-entry-signal-model.md`](../plans/done/ml-entry-signal-model.md).
