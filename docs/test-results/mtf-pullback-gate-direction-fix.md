# MTF Pullback Gate Direction Fix — Re-Validation

Date: 2026-09-20

## Setup

- **Follow-up to** [mtf-entry-indicator-ablation.md](mtf-entry-indicator-ablation.md), which found RSI and SMA produce degenerate, near-one-directional signal streams as the MTF M1 entry trigger (RSI: 0/218 buy; SMA: 161/186 buy), while MACD (the live default) looked healthy (49/96).
- **Root cause found by direct instrumentation** (diagnostic script, not committed): `MultiTimeframeStrongSignalStrategy._pullback_completed` (`app/signals/strategies/multi_timeframe.py`) is applied identically to both buy and sell candidates, but only ever checks one pattern — *price was below its M1 SMA20 ~20-25 bars ago and has since closed back above it* (a bullish crossover). There was never a mirror check for sell candidates (price was above, now below). Measuring the gate's pass rate split by pre-gate direction (before the gate is applied) confirmed this is what drove the earlier skew, not the raw indicators' win-rate quality:

  | Entry | Pre-gate BUY → gate pass rate | Pre-gate SELL → gate pass rate |
  |---|---|---|
  | MACD | 315 → 44.1% | 391 → 43.5% |
  | RSI | 410 → **0.7%** | 560 → **74.6%** |
  | SMA | 692 → **81.1%** | 892 → **5.4%** |

  RSI's oversold/buy trigger fires while price is still falling (the opposite of the bullish shape the gate demands, hence 0.7%); its overbought/sell trigger fires after a rally (which usually *is* that bullish shape, hence 74.6%) — the gate silently converts RSI into a sell-only trigger. SMA's bullish crossover often *is* almost exactly the pattern being checked (81.1%); its bearish crossover looks nothing like it (5.4%). MACD's timing doesn't correlate tightly with that specific shape either way, so it passed both sides at a similar, middling rate — balanced by coincidence, not because MACD's signal quality is actually better.
- **Fix**: `_pullback_completed` now takes the candidate `direction` and, for `"sell"`, checks the true mirror condition (price was above SMA20 ~20-25 bars ago, has since closed back below it) instead of reusing the bullish check. Buy-direction logic is unchanged.
- **Re-ran the same three configs** (target=8/stop=5, session filter, ADX gate — today's live `Config` defaults, same as the original ablation) against the fixed gate.

## Results

| Entry | Old gate — signals (buy/sell) | Old gate win rate | New gate — signals (buy/sell) | New gate win rate |
|---|---|---|---|---|
| MACD (live) | 145 (49/96) | 46.7% | 124 (49/75) | **33.6%** |
| RSI | 218 (0/218) | 43.0% | **0** | n/a |
| SMA | 186 (161/25) | 38.1% | 403 (161/242) | **39.5%** |

Breakeven at target=8/stop=5 is 5/13 ≈ **38.5%**.

## Findings

1. **The fix is correct and does what it says** — it's no longer a bullish-only filter masquerading as direction-aware entry timing. But "more correct" and "better backtest result" are not the same thing here, and they diverge sharply:
2. **MACD — the currently-live entry indicator — drops from solidly-profitable (46.7%) to below breakeven (33.6%) once its sell signals are gated by a genuinely bearish pattern instead of a bullish one.** Buy count is unchanged (49, since buy logic didn't change); sell count drops from 96 to 75, and the *win rate on the surviving sells is worse*, not better — i.e., the MACD sells that used to pass through the old (wrong) bullish-shaped gate were, empirically, better trades than the MACD sells that pass the new (correct) bearish-shaped gate. This is a genuinely surprising result, not an artifact of the fix being wrong: chasing a fresh bearish crossover on M1 apparently performed worse than fading a small bullish blip did, in this specific window.
3. **RSI now produces zero signals in the entire 4-week window.** Its buy timing already failed the (unchanged) bullish check before; its sell timing — which used to slip through only because the old check didn't actually care about direction — now correctly fails the bearish check too (RSI overbought/sell fires *after* a rally, which is the opposite of "recently turned bearish"). RSI's contrarian timing is fundamentally incompatible with an SMA-crossover-shaped entry-timing gate in either direction under this MTF setup — this isn't a tuning issue, it's a structural mismatch.
4. **SMA becomes the standout: 403 signals, genuinely bidirectional (161 buy / 242 sell) for the first time, and the only config that clears breakeven (39.5% vs 38.5%).** Its bearish crossover timing (price crossing below its own MAs) matches the corrected bearish-pattern gate well, the same way its bullish crossover already matched the bullish gate — so fixing the asymmetry let its natural sell signals through for the first time, more than tripling its total signal count in the process.
5. **The ranking from the (buggy) ablation report is now inverted for the two indicators that still produce signals: SMA > MACD, not MACD > SMA.** The original report's "keep MACD" conclusion no longer holds as stated.
6. **Heavy caveats before acting on this**: single 4-week window (same one used throughout this whole investigation's early rounds, which explicitly needed a second out-of-sample window before earlier findings were trusted — that hasn't been done here yet); sample sizes are modest per direction (75-242 trades); MACD's regression and SMA's improvement could both partly reflect this specific month's price action interacting with a very specific 20-25-bar SMA lookback, not a durable property. This needs a second, non-overlapping window (same discipline as `spread-and-second-window-validation.md`) before treating "swap live to SMA" as settled.

## Open decision — not resolved here

`Config`'s live M1 entry indicator is still MACD (`USE_MULTI_TIMEFRAME_SIGNALS=True`, no `indicators` override in `strategy_factory`'s MTF path) and the app places real orders on the demo account when run. This report does not change that wiring. Three live options, in increasing order of change:
- **Leave the gate bug in place, keep MACD** — known-quantity behavior (46.7% in this window), but knowingly running on a gate that doesn't do what its own docstring says.
- **Ship the gate fix, keep MACD** — more correct code, but this backtest says the live entry indicator would now be under breakeven; not something to ship without deciding what to do about that.
- **Ship the gate fix and switch the live entry indicator to SMA** — the combination that backtests best here, but on one window only, with no second-window or spread-cost re-check yet (this session used the confirmed-real zero spread from `zero-spread-retune.md`, so spread isn't the open question — window generalization is).

None of these three has been chosen — this needs the user's call before any live-affecting config changes are made.
