# Second-Window Validation of the -$5 Cap — Ruled Out

Date: 2026-09-21

## Setup

- Direct second-window check of `cap-sweep-5.md`'s SMA result (+$49.60, the first net-positive full-lifecycle result in this investigation), which was already flagged as needing independent-window confirmation before meaning anything.
- Ran the identical config (`--full-lifecycle --trail-gap-pct 0.10 --trail-gap-floor-money 0.0 --post-be-loss-cap 5.0`) on the preceding, non-overlapping 4-week window (`--start-pos 28801`, this project's standard second window), both indicators.

## Results — full 2×2

| Entry | Window 1 | Window 2 |
|---|---|---|
| SMA | **+$49.60**, 63.6% win, 106 capped trades avg -$5.11 | **-$299.00**, 60.5% win, 156 capped trades avg -$5.42 |
| MACD | -$42.60, 62.4% win, 33 capped trades avg -$5.18 | -$50.20, 65.9% win, 37 capped trades avg -$5.18 |

## Findings

1. **SMA's window-1 positive result does not replicate — it fails badly.** Window 2 swings to -$299.00, a large loss, far worse than anything else tested anywhere in this cap-sweep. Win rate stayed similar (60.5% vs 63.6%), but the loss side ballooned (156 capped trades vs 106, at a similar per-trade loss size) enough to overwhelm the trail's gains. **This rules out $5-cap + tight-trail as a real, durable edge for SMA — the window-1 result was a single-window artifact, exactly the outcome the caveat at the time warned was possible.**
2. **MACD, by contrast, is directionally stable across both windows** (-$42.60 and -$50.20 — similar magnitude, same sign). MACD's negative response to this cap value looks like a real, reproducible property of this configuration for MACD, not noise — it's just a negative one.
3. **Overall verdict for the entire cap-sweep investigation, 2 windows × 2 indicators × 4 cap values ($0/$1/$5/$8) with the tight trail: nothing tested has produced a result that is both positive and stable.** Every promising-looking single-window or single-indicator number has either failed to generalize to the other indicator (SMA vs MACD at $5, at $8) or failed to survive a second window (SMA at $5, this test). The $1 cap remains the closest thing to a "default" simply because it's what's already been the reference point throughout, not because it has itself been confirmed superior across two windows in this exact tight-trail configuration — that comparison was never run.
4. **This is a meaningful conclusion in its own right, not just a null result.** It suggests that fine-tuning the post-BE loss cap on one symbol, using 4-week windows, produces swings large enough (SMA's own P&L moved by nearly $350 between two windows at a fixed cap value) that no single-parameter sweep on this data is going to reliably separate a real improvement from noise. Two paths forward, neither of which is "keep sweeping cap values on this same data": (a) test on materially more data — more symbols, more or longer windows — before trusting any specific cap value, or (b) treat the current exit design's performance on this account as close to its practical ceiling given the noise floor, and look elsewhere (the earlier open exit-strategy threads, a different symbol, or accepting today's numbers) rather than continuing to chase a specific dollar figure that keeps moving under further testing.

## Caveats

- Still only 2 windows, 1 symbol (EURUSD), 1 lot size. The instability found here doesn't prove no good cap exists — it proves this specific search method (single-symbol, 4-week windows) can't reliably find one with the data available.
- No live Config change made or considered at any point in this cap-sweep thread.
