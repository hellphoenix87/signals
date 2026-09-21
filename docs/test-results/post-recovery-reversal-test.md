# Post-Recovery Reversal Test — Does It Hold, Or Reverse Again?

Date: 2026-09-21

## Setup

- **Direct follow-up to `post-cap-recovery-test.md`**, which found 78-85% of trades cut by the -$1 cap would have recovered to breakeven or better if left alone — but left one question explicitly open: what happens *after* that recovery? Does the trade continue to a real profit, or just touch $0 and reverse again?
- **Extended the same counterfactual**: for every trade that recovers (per the existing `cf_recovered_to_be` tracking), continue watching from the recovery point onward — no new tick fetch, same real data already in memory — tracking the running peak reached after recovery (`cf_post_recovery_peak`) and whether profit ever falls back below $0 again after that point (`cf_reversed_after_recovery`).
- Same window/gate, original settings (60%/$2 trail, $1 cap) matching `post-cap-recovery-test.md` exactly, both indicators.

## Results

| Entry | Recovered trades | Reversed again | Avg peak before 2nd reversal | Held/climbed | Avg peak reached |
|---|---|---|---|---|---|
| SMA | 214 | **209 (97.7%)** | **$5.48** | 5 (2.3%) | $11.32 |
| MACD | 57 | **55 (96.5%)** | **$4.46** | 2 (3.5%) | $7.50 |

## Findings

1. **Direct, honest answer: yes, the overwhelming majority of recovered trades (96.5-97.7%) do eventually reverse again.** "Recovers to breakeven and then holds or climbs forever" is rare (2.3-3.5%). This is not a case for "just let every recovering trade run indefinitely."
2. **But before that second reversal, they reach a real, substantial average peak — $5.48 (SMA) / $4.46 (MACD).** This is not a fleeting touch of $0.01 before falling back — it's a genuine, sizable window where the trade is meaningfully profitable, comparable in magnitude to the raw entry-excursion peaks measured earlier in this whole investigation (`entry-excursion-trace.md`: $4.73-6.12 average peak from entry). This directly explains why `loose-cap-tight-trail-test.md`'s $8 cap saw such a large jump in `trailing_breach_pct_of_peak` captures (SMA 108→267, MACD 38→79): most recovering trades really do build a real peak that a live trail can catch, even though the trade is "doomed" to reverse again if left unwatched.
3. **This refines, rather than resolves, the still-open loss-cap question.** The $8 cap tested was arbitrary (matched the overall average drawdown, not this recovery-specific peak). The real trade-off is now clearer: give a trade enough room to reach roughly its ~$4.46-5.48 average peak (where the tight trail can capture ~90% of it, ~$4-5 per successful trade) without extending so much room that the ~15-22% of trades that never recover cost too much per loss. **A cap in the $3-6 range, informed by this peak size rather than the drawdown size, is a better-motivated starting point for the still-untested sweep** than the $8 tested — but this needs to actually be run, not inferred from this number alone, since the cap also determines how many trades survive long enough to reach that peak in the first place (a tighter cap than the recovery peak itself would cut some before they get there).
4. **Both indicators show the same shape** (post-recovery peak roughly $1-2 larger than the average worst-point drawdown measured in `post-cap-recovery-test.md`), suggesting this isn't specific to one entry indicator's timing.

## Caveats

- Single 4-week window, same as everything else this session.
- Small sample for the "held/climbed" group (5 SMA, 2 MACD trades) — the $11.32/$7.50 average peaks for that group are not statistically reliable on their own.
