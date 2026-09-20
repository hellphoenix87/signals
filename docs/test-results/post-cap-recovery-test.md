# Post-Cap Recovery Test — Would Capped Trades Have Come Back?

Date: 2026-09-20

## Setup

- **User's question**: for a trade cut by the post-BE -$1 loss cap, would it have recovered to breakeven or better if just left alone instead?
- **Mechanism**: `mt5.copy_ticks_from` already fetches the full tick budget (4,000) up front, independent of where the real rule-driven replay actually stops — so the ticks *after* a cap-triggered exit are already sitting in memory. Added a pure post-hoc counterfactual: for every `profit_drop_after_be` outcome, continue watching the same real tick stream from the exit point, with no exit rule applied at all, tracking whether profit ever reaches ≥$0 again (`cf_recovered_to_be`), how many ticks that took (`cf_ticks_to_recover`), and the worst point reached along the way (`cf_min_profit_after_exit`). No new tick fetch, no live Config change — pure observation of data already collected during the real replay.
- Same window/gate, original `Config`-matching settings (60%/$2 trail, $1 cap) — this counterfactual is about the cap that's actually live-relevant, not the tight-trail variants tested earlier.

## Results

| Entry | `profit_drop_after_be` trades | Recovered to BE+ | Avg ticks to recover | Never recovered | Avg worst point (all) | Avg worst point (non-recoverers) |
|---|---|---|---|---|---|---|
| SMA | 250 | **213 (85.2%)** | 348.7 | 37 (14.8%) | -$6.96 | -$13.49 |
| MACD | 73 | **57 (78.1%)** | 439.4 | 16 (21.9%) | -$8.11 | -$14.18 |

For reference, the cap's own current average resolution time is 183.8 ticks (SMA) / 222.9 ticks (MACD) — roughly **half** the time recovery actually takes.

## Findings

1. **Most trades cut by the -$1 cap would have come back** — 85.2% (SMA) and 78.1% (MACD) eventually reach breakeven or better if left alone. In that narrow sense, the current cap is "too eager": it's not distinguishing doomed trades from ones that were simply taking longer than the cap's patience allows.
2. **But that recovery isn't free or fast.** It takes roughly 2x as long as the cap's current average resolution time, and — critically — the trade typically falls much deeper into loss first: average worst point around -$7 to -$8, and -$13 to -$14 for the ~1-in-5 that never come back at all. "Wait for it" means tolerating a real, sometimes large drawdown, not a guaranteed free ride back to $0.
3. **This directly explains why `be-floor-trail-test.md`'s $0 cap made things so much worse.** Both the current $1 cap and the tested $0 cap sit on the "too early" side of a recovery curve that typically needs 350-440 ticks and a $7-8 dip to complete — a $0 cap is simply further in that direction (cutting even sooner, catching even more trades before any chance of the recovery this data shows is common), not a fix for the underlying timing mismatch.
4. **This is a real risk/reward tradeoff, not a discovered inefficiency to simply correct.** A much looser cap (say -$5 to -$10) would likely convert many of these -$1.13/-$1.14 losses into $0-or-better outcomes — but it also means every post-BE trade now risks a $7-14 drawdown instead of a $1 one, a large increase in per-trade risk that this data alone doesn't justify as worthwhile without knowing the full downstream picture. Two things this counterfactual specifically does *not* tell us:
   - **What happens after recovering to breakeven** — does the trade then continue on to a real profit, or just touch $0 and reverse again into another loss? That's a different, unanswered question this test wasn't designed to measure.
   - **The actual P&L outcome of a properly-tuned looser cap** — this only measures the binary "recovers or not" and the drawdown involved, not what a specific alternative cap value would have produced in total dollars. That needs a real cap-value sweep (e.g. -$3/-$5/-$7/-$10 combined with the tight trail, mirroring the methodology already used for `full-lifecycle-backtest.md`'s original cap retune), not an inference from this counterfactual alone.

## Caveats

- Single 4-week window, same as everything else this session.
- This is a promising lever worth pursuing further — empirically-measured recovery behavior, not a guess — but "loosen the cap" is not yet a recommendation; it's a hypothesis this data makes worth testing directly via a real cap sweep before drawing a conclusion.
