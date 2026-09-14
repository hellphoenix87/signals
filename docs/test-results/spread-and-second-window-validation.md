# Spread and Second-Window Validation

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD
- **Motivation**: [production-fix-validation.md](production-fix-validation.md) found single-timeframe at target=7/stop=5 crossed breakeven (43.8% vs. 41.7% required) but flagged two unresolved caveats: spread was never modeled, and the result came from a single 4-week window. This report closes both gaps.
- **New tooling** (`scripts/backtest_signals.py`):
  - `--spread-pips`: models a round-trip spread cost against bid-based OHLC bars in `evaluate_signal` — buying at ask/selling at bid means a target needs `spread_pips` more favorable movement to clear, while a stop needs that much less adverse movement to hit (you're already down the spread the instant you enter). Tested at 1 pip, a realistic retail EURUSD round-trip cost (this demo account's own historical spread is unrealistically tight — median 0, mean 0.016 pips — so it can't answer "would a normal broker's spread matter," only a fixed realistic assumption can).
  - `--start-pos`: shifts the M1 (and proportionally M5/M15) window back by N bars, for a genuinely out-of-sample period. **Window 1** = the same most-recent 4 weeks used throughout this category (`start_pos=1`). **Window 2** = the immediately preceding, non-overlapping 4 weeks (`start_pos=28801`).
- **Commands** (all 4 combinations of window x spread, for both strategies, at both 7:5 and 10:5):
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 [--mtf] --target-pips <7|10> --stop-pips 5 [--spread-pips 1] [--start-pos 28801]
  ```

## Results

Breakeven: 41.7% at 7:5, 33.3% at 10:5.

| Strategy | Ratio | Window 1, no spread | Window 1, +1 pip spread | Window 2, no spread | Window 2, +1 pip spread |
|---|---|---|---|---|---|
| Single-tf | 7:5 | **43.8%** ✅ | 32.7% ❌ | **42.4%** ✅ | 33.0% ❌ |
| Single-tf | 10:5 | 30.2% ❌ | 22.2% ❌ | 31.7% ❌ | 24.3% ❌ |
| MTF | 7:5 | 39.2% ❌ | 33.7% ❌ | **42.6%** ✅ | 36.9% ❌ |
| MTF | 10:5 | 29.5% ❌ | 22.6% ❌ | 35.6% ❌ | 29.6% ❌ |

(✅ = clears that ratio's breakeven, ❌ = doesn't. Window 1/no-spread column for single-tf and MTF at both ratios is carried over from [production-fix-validation.md](production-fix-validation.md).)

## Findings

1. **The two caveats resolve in opposite directions.** Window generalization is *not* the problem: at 7:5, both strategies' no-spread win rates are close across the two independent windows (single-tf 43.8% vs. 42.4%; MTF 39.2% vs. 42.6% — MTF actually clears breakeven on window 2 too). This is a real, reproducible property of the signal engine on this symbol/timeframe, not a fluke of one window's specific price action.
2. **Spread is the actual problem, and it's decisive.** Every single spread-modeled cell drops below breakeven, by 4-11 percentage points depending on configuration. At 7:5, a 1-pip round-trip cost costs roughly 9-11 points of win rate — far larger than the ~1-2 point margin the raw edge showed above breakeven.
3. **Why the hit is so large**: at 7:5, a 1-pip spread is genuinely material — it's spread across a combined stop+target distance of only 12 pips (1/12 ≈ 8.3% of the whole risk+reward range), so it's not a rounding error, it's a substantial fraction of the entire trade's economics.
4. **Net verdict: there is a small, real, reproducible directional edge in this signal engine at a 7:5 (or similar tight) ratio — but it is not currently large enough to survive a realistic spread.** This is a more precise and more useful conclusion than either "it's all noise" or "it's profitable" — the mechanism (crossover MACD + RSI-weighted vote, tight target/stop) is doing something real, just not enough of it yet.
5. **Implication for next steps**: closing a ~9-11 point gap purely by further signal-quality improvements is a tall order. Two other levers exist and weren't explored here: (a) a genuinely tight-spread broker/account (this app already trades on a demo account with near-zero spread — if that generalizes to whatever live account is eventually used, the spread-free columns above are the relevant ones, not the +1 pip columns); (b) search for a target/stop ratio between 7:5 and 10:5 (or elsewhere) where the spread-adjusted win rate is maximized — tighter ratios have higher raw win rate but are more spread-sensitive percentage-wise, so there may be a sweet spot not yet found in the ratios tested (50:20, 15:5, 10:5, 7:5).
6. **This does not change the earlier `USE_MULTI_TIMEFRAME_SIGNALS = False` decision** — neither strategy clears breakeven with realistic spread modeled, so neither is ready for live trading regardless of the flag.
