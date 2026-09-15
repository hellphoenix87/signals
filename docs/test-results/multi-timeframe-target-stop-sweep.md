# Multi-Timeframe (SMA/M15, RSI/M5, MACD/M1) — Target/Stop Sweep

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD
- **Window**: 4 weeks of M1 history, plus synchronized M5 (~5,760 candles) and M15 (~1,920 candles) history covering the same calendar span, all fetched live from `MetaQuotes-Demo`.
- **Strategy under test**: `MultiTimeframeStrongSignalStrategy` via `strategy_factory(config=Config, use_multi=True)` — SMA bias on M15 (gated by an ADX trend-strength filter), RSI confirm on M5, MACD entry on M1, combined via weighted confluence scoring against `MTF_SCORE_THRESHOLD`, plus a pullback-above-SMA20 entry-timing gate. This strategy already existed (`app/signals/strategies/multi_timeframe.py`, prior `mtf-signal-folding` plan) but had never been backtested — `Config.USE_MULTI_TIMEFRAME_SIGNALS = False` throughout, since enabling it live was explicitly deferred pending exactly this kind of validation.
- **New tooling**: `scripts/backtest_signals.py --mtf` (added this session) replays this strategy by fetching M1/M5/M15 history up front and, for each M1 decision point, feeding each higher-timeframe layer only the candles already closed by that point (`bisect` over each timeframe's precomputed close-time array) — independent of the live `USE_MULTI_TIMEFRAME_SIGNALS` flag, which stays off regardless of this flag. Causal alignment (no lookahead) was spot-checked directly: at 4 sampled M1 timestamps, every M5/M15 candle visible to the strategy had already closed by that point, and the next one (if any) closed strictly later.
- **Evaluation method**: fixed target/stop simulation (`evaluate_signal`), same as the single-timeframe reports, for direct comparison.
- **Command** (repeated for `--stop-pips` in `5 10 20`):
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --stop-pips <5|10|20>
  ```
  `--target-pips` left at default (`Config.DEFAULT_TP_PIPS = 50`).
- **Spread**: not modeled (same as the single-timeframe reports).

## Results

| Stop (pips) | Target (pips) | Signals (buy/sell) | Wins | Losses | Undecided | Win rate (decided) | Avg bars to resolution |
|---|---|---|---|---|---|---|---|
| 5 | 50 | 846 (621/225) | 12 | 613 | 221 | 1.9% | 77.4 |
| 10 | 50 | 846 (621/225) | 18 | 322 | 506 | 5.3% | 129.5 |
| 20 | 50 | 846 (621/225) | 18 | 91 | 737 | 16.5% | 151.5 |

Same window/symbol as [single-timeframe-macd-only-baseline.md](single-timeframe-macd-only-baseline.md) (16,082 signals) and [single-timeframe-macd-sma-rsi.md](single-timeframe-macd-sma-rsi.md) (5,657 signals) for direct comparison.

## Findings

1. **Far more selective**: 846 signals over 4 weeks vs. 5,657-16,082 for the single-timeframe variants — the weighted confluence + ADX gate + pullback-timing filter is doing real filtering work, not just trimming noise.
2. **Beats both single-timeframe variants at every matched stop size** — e.g. at stop=20, 16.5% vs. the baseline's 7.1% win rate.
3. **Still not net-profitable at any stop size tested against `DEFAULT_TP_PIPS = 50`.** Even the best result (stop=20, 16.5%) is well under the ~28.6% breakeven a 20:50 risk/reward requires — and 87% of those signals never resolved within the 300-bar lookahead window, so that figure rests on a small decided sample (n=109).
4. **Average bars-to-resolution creeps to 151 (~2.5 hours) at stop=20** — suggests the 50-pip target may simply be too far for M1 EURUSD to reach reliably within a useful timeframe, independent of the stop. Not tested in this report; flagged as a follow-up (a tighter, more matched target, e.g. 10-15 pips).
5. **Decision made** (user's call, not automated): `Config.USE_MULTI_TIMEFRAME_SIGNALS` stays `False`. Multi-timeframe is directionally better than the single-timeframe path but not yet profitable at any tested configuration — not ready for live trading either way.
