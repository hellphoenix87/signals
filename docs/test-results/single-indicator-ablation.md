# Single-Indicator Ablation

Date: 2026-09-14

## Setup

- **Symbol**: EURUSD, 4 weeks of M1 history
- **Motivation**: the single-timeframe path has only ever been tested as a MACD+SMA+RSI majority vote. This isolates each indicator alone to see whether one carries real individual edge that the combined vote might be diluting, or whether all three are equally weak.
- **New tooling**: `--indicators <name>` (added this session) overrides the default vote with only the named indicator(s), reusing `app/signals/signal_generation.py::build_indicator` — the same construction production code uses, so these are genuinely the production indicators in isolation, not reimplementations.
- **Target/stop**: 15/5 pips (3:1 R:R, breakeven 25%), matching [target-stop-realistic-resweep.md](target-stop-realistic-resweep.md) for direct comparison.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --indicators macd --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --indicators sma --target-pips 15 --stop-pips 5
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --indicators rsi --target-pips 15 --stop-pips 5
  ```

## Results

| Indicator | Signals (buy/sell) | Win rate | vs. combined vote (16.2%, 5,657 signals) |
|---|---|---|---|
| MACD only | 16,070 (8093/7977) | 15.6% | slightly worse |
| SMA only | 9,436 (4724/4712) | 16.8% | slightly better |
| RSI only | 6,799 (3397/3402) | 17.8% | better |
| Combined (macd+sma+rsi) | 5,657 | 16.2% | — |

## Findings

1. **RSI alone has the best individual win rate (17.8%)** with the fewest signals of the three — the most selective and (relatively) highest-quality single indicator tested.
2. **The combined 3-way vote (16.2%) sits between MACD-only and SMA-only, not above RSI-only** — the current equal-weight majority vote isn't amplifying RSI's relatively stronger signal, it's averaging it down toward the weaker MACD/SMA votes.
3. **All three individually, and combined, remain below the 25% breakeven** for this 3:1 ratio — no single indicator tested here is independently profitable, so this doesn't point to an easy "just use RSI" fix, but it does suggest the current equal-weight voting scheme may not be the right way to combine them if RSI's edge is real and reproducible.
4. **Follow-up worth considering, not tested here**: weight RSI more heavily than MACD/SMA in the vote (or require RSI's agreement specifically, rather than simple majority), and re-test — currently every indicator counts equally regardless of this result.
