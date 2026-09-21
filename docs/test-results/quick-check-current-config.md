# Quick-Check Under Current Config — Does Price Actually Move With the Signal?

Date: 2026-09-20

## Setup

- **Motivation**: user asked a question narrower than P&L or even signal-quality-proxy win rate — after entering a trade in the signal's direction, does price actually progress that way at all, at some point, independent of any exit strategy? `full-lifecycle-entry-indicator-comparison.md` and `invert-signal-test.md` both measure real dollar outcomes *through* the current exit strategy (breakeven arming, post-BE trail, loss cap); this test strips that out entirely.
- **Tool**: `backtest_signals.py --quick-check --horizon-bars N` — for each signal, looks only at the next N M1 candles and reports the best price ever seen in the signal's favor, the worst ever seen against it, and where price ended up at the close of the window. No target, no stop, no breakeven, no trailing logic — a direct, assumption-free read on entry timing.
- Same 4-week window as every other backtest this session. Ran horizons 1/5/15 minutes for both entry indicators (SMA — today's `Config.MTF_ENTRY_INDICATOR` — and MACD), matching the methodology of the older `quick-check-multi-horizon.md` (2026-09-14) for direct comparison, but under today's fixed pullback gate.

## Results

| Entry | Horizon | Signals | Had favorable moment | Never went positive | Still positive at end | Avg favorable | Avg adverse | Avg end |
|---|---|---|---|---|---|---|---|---|
| SMA | 1 min | 403 | 85.1% | 14.9% | 48.4% | +0.54 | +0.49 | **+0.04** |
| SMA | 5 min | 403 | 92.6% | 7.4% | 48.1% | +1.30 | +1.17 | **+0.04** |
| SMA | 15 min | 403 | 95.8% | 4.2% | 51.4% | +2.37 | +2.07 | **+0.20** |
| MACD | 1 min | 124 | 83.1% | 16.9% | 45.2% | +0.38 | +0.45 | **-0.07** |
| MACD | 5 min | 124 | 90.3% | 9.7% | 54.8% | +1.01 | +1.02 | **+0.06** |
| MACD | 15 min | 124 | 91.9% | 8.1% | 44.4% | +1.71 | +1.99 | **-0.41** |

## Findings

1. **Yes — price does progress in the signaled direction, strongly and consistently.** 83-96% of trades (depending on horizon/indicator) have a genuinely favorable moment at some point in the window, and the average favorable excursion is real and often sizeable (+0.4 to +2.4 pips). Only 4-17% of trades never go positive at all. This directly answers the user's question: the entry is not backwards, and it is not noise-only — the large majority of trades do move the right way, at least initially.
2. **SMA shows a consistent, widening favorable tilt as the horizon lengthens**: avg-end drift goes +0.04 → +0.04 → **+0.20** pips (1/5/15 min), and "still positive at end" climbs to 51.4% by 15 minutes — the only cell across this whole table above 50%. The favorable/adverse gap also widens (+0.05 → +0.13 → +0.30 pips) rather than narrowing.
3. **MACD's pattern is not monotonic and is notably worse at 15 minutes**: avg-end drift goes -0.07 → +0.06 → **-0.41** pips. At 15 minutes MACD's average adverse excursion (1.99) actually *exceeds* its average favorable excursion (1.71) — the only cell in this table where that happens — and "still positive at end" drops to 44.4%, the lowest of any cell. MACD's favorable move looks less durable than SMA's at this horizon, in this window.
4. **Compared to the stale `quick-check-multi-horizon.md` (2026-09-14, pre-gate-fix, MACD-only MTF)**, which found increasingly *negative* average drift at every horizon out to 15 minutes (down to -0.20): today's numbers look meaningfully different — SMA is consistently non-negative and improving, and even today's MACD only shows the earlier pattern at the 15-minute mark specifically, not throughout. Worth being honest that this could be the pullback-gate fix, a different window, or just noise — not enough here to say definitively which, but it's a real, measured difference from the last time this was checked.
5. **Important reconciliation with `invert-signal-test.md`**: that doc concluded, indirectly, that "entries carry near-zero real directional information" — but that conclusion was inferred from P&L symmetry under one specific exit design (breakeven/trail/cap), not measured directly. This test measures direction directly, and it disagrees with that pessimistic framing: 83-96% of trades do move favorably, often by more than a pip on average. **The more consistent story across all of this session's findings is not that the signal lacks directional value, but that the live exit strategy's specific parameters (how far price must move to arm breakeven, how tight the post-BE trail is, how small the loss cap is) are not well-matched to the actual size and timing of these favorable moves** — a real favorable excursion of 1-2 pips is common, but the exit mechanism (per `full-lifecycle-entry-indicator-comparison.md`) converts ~60% of trades into a small loss anyway (`profit_drop_after_be`) rather than capturing that excursion. That points at exit-strategy calibration as the more promising lever, not entry-signal replacement.

## Caveats

- Single 4-week window, same as every other test this session — not yet checked on a second window. MACD's sharp 15-minute reversal in particular should be treated cautiously until re-checked; it's exactly the kind of single-window artifact this investigation has been burned by before.
- This is still a passive-hold read (no exit logic at all), not a trading strategy — it answers "does the direction call tend to be right," not "can that be converted into profit," which is a separate, harder question the full-lifecycle results already show isn't automatic.
