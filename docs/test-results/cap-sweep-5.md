# Cap Sweep: -$5 — SMA Turns Positive, MACD Does Not

Date: 2026-09-21

## Setup

- **Sweep point motivated by `post-recovery-reversal-test.md`**: trades that recover from the -$1 cap reach a real average peak of $4.46-5.48 before (usually) reversing again. A cap sized to that recovery peak, rather than the $8 overall-drawdown figure tested previously, should let more trades survive to reach it without overpaying on the ones that don't recover.
- Tested `--post-be-loss-cap 5.0` combined with the tight trail (10% of peak, no floor), same window/gate as every other test this session, both indicators. No new code — reuses existing overrides.

## Results

| Entry | Config | Total P&L | Win rate | `trailing_breach_pct_of_peak` | `profit_drop_after_be` |
|---|---|---|---|---|---|
| SMA | $1 cap | -$37.00 | 28.8% | 116, avg +$2.78 | 250, avg -$1.14 |
| SMA | **$5 cap** | **+$49.60** | **63.6%** | **259, avg +$2.66** | 106, avg **-$5.11** |
| SMA | $8 cap | -$47.40 | 66.1% | 267, avg +$2.68 | 68, avg -$8.28 |
| MACD | $1 cap | -$19.20 | 31.5% | 39, avg +$2.19 | 73, avg -$1.13 |
| MACD | **$5 cap** | **-$42.60** | **62.4%** | **77, avg +$1.98** | 33, avg **-$5.18** |
| MACD | $8 cap | -$93.00 | 64.5% | 79, avg +$1.98 (see prior doc) | 25, avg -$8.57 |

## Findings

1. **SMA at $5 is the first genuinely net-positive full-lifecycle result in this entire investigation: +$49.60, 63.6% win rate.** It beats both the $1-cap baseline (-$37.00) and the $8-cap test (-$47.40) by a wide margin — the sweep-target logic from `post-recovery-reversal-test.md` (size the cap to the recovery peak, not the drawdown) appears to have found a genuinely better point for SMA in this window.
2. **MACD does NOT confirm this — it's -$42.60 at the same $5 cap, actually worse than MACD's own $1-cap baseline (-$19.20).** Win rate improved dramatically for MACD too (62.4% vs 31.5%), but the same "more frequent wins, costlier remaining losses" trade-off that hurt SMA at $8 shows up for MACD at $5 instead — MACD's non-recovering trades are apparently costly enough at this cap size to erase the trail's gains, even though its own recovery-peak data ($4.46 avg) is similar in magnitude to SMA's ($5.48).
3. **This is an important caution, not just a mixed result.** SMA's positive number doesn't generalize even to a second indicator in the exact same 4-week window — it's not yet evidence of a real, durable edge, it's evidence that $5 happens to land in a good spot for SMA's specific entry timing this month. The two indicators' optimal cap values may simply differ (MACD's non-recovery population has looked structurally worse throughout this whole thread — deeper drawdowns, lower recovery rate, longer recovery time in `post-cap-recovery-test.md` — so it may need a tighter cap than SMA to avoid overpaying on trades that were never going to come back).
4. **No live Config change is being made or considered based on this.** A result this sensitive to the exact cap value (P&L swung from -$37 → +$49.60 → -$47.40 for SMA across just three tested points) is exactly the kind of finding that needs a second, independent window before it means anything durable — and this one hasn't gotten that yet, on top of already not generalizing across indicators in the same window.

## What this does and doesn't establish

- **Does establish**: a cap somewhere in the $3-8 range, combined with the tight trail, materially changes trade composition (far more winners, fewer but bigger losers) for both indicators — this shape is real and reproducible across both SMA and MACD.
- **Does NOT establish**: that $5 (or any single value tested so far) is *the* right cap — the one clearly positive result (SMA, $5) is unconfirmed by the second indicator in the same window, and nothing here has been checked against a second time period at all.

## Next steps, not yet done

1. **Second-window validation is now the priority**, more than further sweeping — before spending more effort narrowing the cap value further, confirm whether SMA's +$49.60 at $5 replicates on the preceding 4-week window (`--start-pos 28801`, this project's standard second-window check).
2. If it's worth pursuing further: MACD may need its own, separately-tuned cap rather than sharing SMA's value — worth testing MACD at $3-4 specifically, given its structurally worse non-recovery profile.

## Caveats

- Single 4-week window for every value tested (`$1`/`$5`/`$8`) — the biggest open gap, explicitly called out above, not a footnote.
- No live Config change made or recommended at this stage.
