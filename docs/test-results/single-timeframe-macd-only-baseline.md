# Single-Timeframe (MACD Only) — Baseline Stop Sweep

Date: 2026-09-13/14

## Setup

- **Symbol**: EURUSD
- **Window**: 4 weeks of M1 history (~28,800 candles), fetched live from the `MetaQuotes-Demo` MT5 account via `MarketData.get_historical_candles`
- **Strategy under test**: `StrongSignalStrategy` via `strategy_factory(config=Config)`, single-timeframe path (`Config.USE_MULTI_TIMEFRAME_SIGNALS = False`). At the time of this run, `indicators = {"macd": default_macd_fn}` was the only vote — SMA and RSI were wired into `strategy_factory` but never included in the default single-timeframe indicators dict, so `confidence = total_votes / len(indicators)` was trivially 0 or 1 (this bug is fixed in the next report, [single-timeframe-macd-sma-rsi.md](single-timeframe-macd-sma-rsi.md)).
- **Evaluation method**: fixed target/stop simulation (`evaluate_signal`) — for each buy/sell signal, look forward up to 300 bars and report whether price moved `--target-pips` in its favor before `--stop-pips` against it.
- **Command** (repeated for `--stop-pips` in `5 10 20`):
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --stop-pips <5|10|20>
  ```
  `--target-pips` left at default (`Config.DEFAULT_TP_PIPS = 50`).
- **Spread**: not modeled by the backtest (entry price is the candle's `close`, no bid/ask cost applied anywhere in `evaluate_signal`).

## Results

| Stop (pips) | Target (pips) | Signals (buy/sell) | Wins | Losses | Undecided | Win rate (decided) | Avg bars to resolution |
|---|---|---|---|---|---|---|---|
| 5 | 50 | 16,082 (8094/7988) | 37 | 10,326 | 5,719 | 0.4% | 91.8 |
| 10 | 50 | 16,082 (8094/7988) | 62 | 5,502 | 10,518 | 1.1% | 137.0 |
| 20 | 50 | 16,082 (8094/7988) | 131 | 1,706 | 14,245 | 7.1% | 164.9 |

(Signal count is identical across rows since only the exit resolution changes, not signal generation.)

## Findings

1. **Massive overtrading**: 16,082 signals out of ~28,800 candles means the strategy fires buy/sell on **more than half of all M1 candles** — not selective signal identification, closer to noise-tracking.
2. **Root cause identified**: `calculate_macd`'s trigger (`app/signals/indicators/macd.py`) is "histogram positive/negative and larger in magnitude than the previous candle's" — an *accelerating-histogram* condition, not a true crossover (sign flip or MACD/signal-line cross). On any candle where the histogram is already positive, whether it grows or shrinks from the prior candle is close to a coin flip in noisy M1 data, so it re-fires on nearly every candle throughout a positive-histogram stretch, not just at its start.
3. **Compounding wiring bug**: with MACD as the only active indicator, `confidence = total_votes / len(indicators)` is always exactly 0 or 1 — `CONFIDENCE_THRESHOLD = 0.5` gates nothing, since there's no second indicator to ever disagree with it.
4. **Win rate rises with stop size (0.4% -> 7.1%) but never approaches breakeven** for a 50-pip target at any of the three stops tested (would need ~9%/17%/29% respectively) — widening the stop reduces noise-driven stop-outs but doesn't fix the underlying lack of directional edge.
5. Directly motivated the fix in the next report: activate SMA+RSI alongside MACD so `confidence` reflects genuine multi-indicator agreement instead of a single rubber-stamped vote.
