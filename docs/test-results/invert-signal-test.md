# Invert-Signal Test — Does Trading Opposite the Signal Do Better?

Date: 2026-09-20

## Setup

- **Motivation**: `full-lifecycle-entry-indicator-comparison.md` found the dominant loss driver for both MACD and SMA is `profit_drop_after_be` (~59-62% of trades): the trade *does* move favorably enough to arm breakeven, then reverses and gets cut for a small loss before the trailing stop can lock anything in. User hypothesis: if trades reliably confirm-then-reverse, does literally trading the opposite of the signal do better?
- **New tooling**: `--invert-signal` added to `scripts/backtest_exit_strategy.py` — flips `final_signal` (buy↔sell) immediately after the hold-check, before any simulation or recording, so `direction` in the CSV/summary reflects what was actually traded. Everything else (gate, window, exit config) identical to the non-inverted baselines already established.
- Same 4-week window, same fixed pullback gate, same exit config (`EXIT_POST_BE_LOSS_CAP_MONEY=1.0`, `pct60_floor2` trail) as `full-lifecycle-entry-indicator-comparison.md`, for both SMA and MACD.

## Results

**Headline totals (as printed by the script):**

| Config | Trades | Total P&L | Win rate |
|---|---|---|---|
| SMA normal | 403 | -$91.60 | 28.8% |
| SMA inverted | 403 | -$62.60 | 26.8% |
| MACD normal | 124 | -$21.60 | 31.5% |
| MACD inverted | 124 | -$1.60 | 25.8% |

Taken at face value, inverting looks like an improvement in both cases — smaller loss, MACD-inverted nearly flat. **This is misleading.** A handful of trades in each run were still open at the 4,000-tick cutoff (`exhausted`, mark-to-market at an arbitrary point, not a real closed outcome) and swung by tens of dollars on very few trades:

| Config | `exhausted` trades | `exhausted` total P&L |
|---|---|---|
| SMA normal | 8 | +$83.00 |
| SMA inverted | 12 | +$164.40 |
| MACD normal | 1 | +$21.40 |
| MACD inverted | 6 | +$70.00 |

**Excluding `exhausted` trades (decided outcomes only — the fair comparison):**

| Config | Decided trades | Decided P&L | P&L/trade |
|---|---|---|---|
| SMA normal | 395 | -$174.60 | -$0.442 |
| SMA inverted | 391 | -$227.00 | **-$0.581** |
| MACD normal | 123 | -$43.00 | -$0.350 |
| MACD inverted | 118 | -$71.60 | **-$0.607** |

**On the decided-only basis, inverting is worse for both indicators, not better.** The apparent headline improvement was entirely an artifact of a few still-open trades happening to have larger favorable snapshots in the inverted runs — not because trading the opposite direction genuinely performs better.

## Findings

1. **Inverting the signal does not produce a better result.** The user's hypothesis (trades reliably confirm-then-reverse, so trade the reversal) does not hold up once censored/still-open trades are excluded from the comparison.
2. **The breakdown shape stays nearly identical whether trading with or against the signal** — `profit_drop_after_be` remains the dominant outcome at 60-63% of trades in all four configs, with very similar average sizes (-$1.13 to -$1.57) regardless of direction or indicator. This is strong evidence the ~60%-cut-for-small-loss pattern is a property of the **exit strategy's own mechanics** (breakeven arming distance, trail floor, loss cap interacting with normal EURUSD M1 tick noise), not of the entry signal's direction.
3. **Combined with the entry-indicator comparison, this points away from "the signal is wrong-way" and toward "the signal carries little real directional information, and the exit strategy's cost structure is what's actually losing money."** Trading with the signal, against it, or (per the entry-indicator comparison) using a completely different indicator all land in a similar place: mostly small losses from a majority of trades, partially offset by fewer, only slightly larger wins from the minority that reach the trailing stop.
4. **Caveat**: single window for all four configs here, same as everything else in this session — not yet checked on a second window. The `exhausted`-trade distortion found here is itself a good argument for not trusting small differences between single-window totals at all; a second window would also carry its own few exhausted trades that could swing things again.

## Decision — no live Config change

This is a diagnostic test only. `Config.MTF_ENTRY_INDICATOR` is unaffected, and no inversion mechanism should be considered for live trading based on this result — it doesn't help. The practical implication is the opposite of what was hypothesized: the fix, if one exists, isn't in flipping direction, it's in the exit strategy's post-breakeven mechanics or in finding an entry signal with genuinely more directional information than these three indicators currently provide.
