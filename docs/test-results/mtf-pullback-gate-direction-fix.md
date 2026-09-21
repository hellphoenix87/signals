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

## Results (window 1 — most recent 4 weeks, `--start-pos 1`)

| Entry | Old gate — signals (buy/sell) | Old gate win rate | New gate — signals (buy/sell) | New gate win rate |
|---|---|---|---|---|
| MACD (live) | 145 (49/96) | 46.7% | 124 (49/75) | 33.6% |
| RSI | 218 (0/218) | 43.0% | **0** | n/a |
| SMA | 186 (161/25) | 38.1% | 403 (161/242) | 39.5% |

Breakeven at target=8/stop=5 is 5/13 ≈ **38.5%**.

## Second-window validation (`--start-pos 28801`, the immediately preceding, non-overlapping 4 weeks)

Window 1 alone wasn't enough to trust — this investigation's own precedent (`spread-and-second-window-validation.md`, and the 7:5 ratio's original validation) required a second, non-overlapping window before treating a result as real rather than a fluke of one month's price action. Re-ran all three entries against the fixed gate on window 2:

| Entry | Window 1 (recent) | Window 2 (preceding) | Pooled |
|---|---|---|---|
| MACD (live) | 124 sig, 33.6% | 129 sig, **43.7%** | 253 sig, **38.7%** (dead on breakeven) |
| RSI | 0 signals | 0 signals | 0 signals |
| SMA | 403 sig, 39.5% | 481 sig, **39.6%** | 884 sig, **39.5%** |

## Findings

1. **The fix is correct and does what it says** — it's no longer a bullish-only filter masquerading as direction-aware entry timing.
2. **RSI is now consistently, robustly unusable — zero signals in both independent windows.** Its contrarian buy/sell timing structurally can't satisfy either mirror of the SMA-crossover-shaped gate. This is the one finding here that needed no hedging even before the second window; it just confirms it.
3. **MACD's post-fix result does NOT replicate — it swings 10 points between windows (33.6% → 43.7%), straddling breakeven in opposite directions each time.** Pooled across both (38.7%) lands almost exactly on breakeven, which isn't a reassuring average — it's masking real instability, not revealing a stable edge. Whatever's driving MACD's win rate under the corrected gate is apparently quite sensitive to which weeks you look at.
4. **SMA replicates tightly: 39.5% in window 1, 39.6% in window 2 — a 0.1-point difference, pooled 39.5% over 884 signals.** This is exactly the kind of cross-window consistency the earlier "not validated enough" caveat was waiting on, and it came back positive. SMA clears breakeven in both windows independently, not just on average.
5. **This reverses the read from the single-window result.** Before the second window, SMA looked like the promising-but-unconfirmed option and MACD looked like a worse-but-at-least-known quantity. With both windows in hand, it's the opposite: SMA is the one entry indicator here with a genuinely reproducible edge under the corrected gate, and MACD is the one whose single-window number (either window's, taken alone) can't be trusted as representative of anything stable.
6. **Remaining caveats**: still only two windows (8 weeks total) on one symbol; the margin is thin in absolute terms (~39.5% vs. 38.5% breakeven, roughly +0.135 pips expected value per trade at these odds, before considering the unmeasured post-breakeven exit phase — see [[project_signals_bot_status]]'s standing "is this bot profitable" caveat); and this whole comparison still doesn't model real trade lifecycle/exits, only the same signal-quality proxy used throughout this investigation.

## Decision: fix the gate, switch the MTF entry indicator from MACD to SMA

`Config`'s live M1 entry indicator is still MACD (`USE_MULTI_TIMEFRAME_SIGNALS=True`, no `indicators` override in `strategy_factory`'s MTF path) and the app places real orders on the demo account when run. **This report does not change that wiring — it recommends a specific change, backed by two-window evidence, for the user to apply.**

- **Ship the gate fix** — real correctness fix, independent of everything else here.
- **Don't keep MACD as the live entry indicator** — its post-fix performance isn't just weak, it's *unstable*: two honest 4-week reads of the same fixed gate disagree by 10 points and straddle breakeven in opposite directions. That's not a foundation to trade on regardless of which single number you'd rather believe.
- **Switch the live entry indicator to SMA** (`strategy_factory`'s MTF path would need an `indicators={"sma": build_indicator("sma", config)}` override, mirroring what `--mtf-entry-indicator sma` already does in the backtest CLI). This is now the only entry indicator tested here with a reproducible, two-window-confirmed edge over breakeven (39.5% / 39.6%), clearing the same bar (independent second-window confirmation) that every other live-`Config`-affecting finding in this investigation (the 7:5→8:5 ratio move, the zero-spread retune, the session filter) was held to.
- The margin is thin and this is still a signal-quality proxy, not a full trade-lifecycle backtest — this is "worth switching to and monitoring," not "proven profitable." Flagged for the user's decision, not applied in this PR, since it's a change to real order-placement behavior (demo account) — same bar this repo held PR #46 to.
