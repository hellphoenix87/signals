# MTF Entry-Layer Indicator Ablation

Date: 2026-09-20

**Superseded in part — see [mtf-pullback-gate-direction-fix.md](mtf-pullback-gate-direction-fix.md).** The RSI/SMA "degenerate, one-sided" pattern noted in finding 2 below turned out to be caused by a real bug in `MultiTimeframeStrongSignalStrategy._pullback_completed` (it only ever checked a bullish pattern, for both buy and sell candidates). After fixing it, the ranking below no longer holds: MACD drops to 33.6% (below breakeven), RSI produces zero signals, and SMA becomes the best-performing, genuinely bidirectional entry layer at 39.5%. Read that report before acting on finding 4's "keep MACD" conclusion.

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history.
- **Motivation**: [single-indicator-ablation.md](single-indicator-ablation.md) found MACD is individually the weakest of the three indicators (RSI 17.8% > SMA 16.8% > MACD 15.6%, single-timeframe, 15:5 target/stop). Yet MTF's M1 entry-trigger layer is MACD-only by construction — bias=SMA/M15, confirm=RSI/M5, entry=MACD/M1, one indicator per layer chosen to avoid duplication across timeframes, not because MACD tested best for that specific role. That ablation was single-timeframe (no M15/N5 gating) at a different ratio (15:5) than live (8:5), so this re-tests the question directly: with the M15 bias and M5 confirm layers held fixed, is MACD, RSI, or SMA the best M1 trigger under today's actual live config?
- **New tooling**: `--mtf-entry-indicator {macd,sma,rsi}` (added this session, `scripts/backtest_signals.py`), requires `--mtf`. Swaps only the M1 entry layer's indicator via `strategy_factory`'s existing `indicators` override — the M15 bias (SMA) and M5 confirm (RSI) layers are built independently of that dict, so they're unaffected. Reuses `build_indicator`, the same production construction, so this is the real indicator in isolation, not a reimplementation.
- **Everything else at today's live `Config` defaults**: target/stop = 8/5 pips, `USE_SESSION_FILTER=True`, `MTF_ADX_MIN_STRENGTH=20.0`, `MTF_SCORE_THRESHOLD=0.6` — no CLI overrides needed for any of these since they're already the live values.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-entry-indicator rsi
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-entry-indicator sma
  ```

## Results

| M1 entry indicator | Signals (buy/sell) | Win rate (decided) | Avg bars to resolution |
|---|---|---|---|
| MACD (live baseline) | 145 (49/96) | **46.7%** | 85.6 |
| RSI | 218 (0/218) | 43.0% | 91.5 |
| SMA | 186 (161/25) | 38.1% | 79.0 |

## Findings

1. **MACD wins under the live MTF config** — the opposite ranking from the single-timeframe ablation. Neither RSI nor SMA beats the current MACD-only entry layer once the M15 bias/M5 confirm gating and today's tighter 8:5 target/stop are in play; swapping the trigger indicator would be a regression, not an improvement.
2. **RSI and SMA both produced degenerate, one-sided signal distributions** in this window — RSI never fired a single buy (0/218, 100% sell) and SMA was 87% buy (161/186). MACD's split (49/96, still sell-skewed but not degenerate) looks far more like a real bidirectional trigger. This is a stronger signal than the win-rate gap alone: an entry layer that can only fire in one direction for an entire 4-week window is not a usable trigger regardless of its win rate on the signals it does produce, since it's silently forfeiting every opposite-direction opportunity the bias/confirm layers would otherwise allow.
3. **Interpretation**: RSI's contrarian 30/70 overbought/oversold logic and SMA's crossover logic, on M1 specifically, appear to interact badly with the M15 bias/M5 confirm gates in a way that collapses them to near-single-direction over this period — plausibly because the higher-timeframe bias was persistently one-sided for the month and RSI/SMA's M1 trigger condition happens to correlate with that bias's direction almost exclusively, where MACD's trend-following logic doesn't. Not confirmed here; would need a longer or differently-trending window to separate "these indicators are structurally bad as an M1 trigger under MTF" from "this specific month was directionally biased."
4. **Conclusion: keep MACD as the MTF M1 entry-layer indicator.** The original design rationale (MACD as the leftover pick after SMA/RSI were claimed by bias/confirm) turns out to also be the empirically better one for this specific role, at least for this window — worth re-running over other periods before treating this as fully settled, but there's no basis here to change the live wiring.
