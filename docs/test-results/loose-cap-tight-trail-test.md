# Loose Cap ($8) + Tight Trail Test — Does Giving Trades Room To Recover Actually Pay Off?

Date: 2026-09-21

## Setup

- **Direct follow-up to `post-cap-recovery-test.md`**: that counterfactual found 78-85% of trades cut by the -$1 cap would have recovered to breakeven or better if left alone, but only after a real ~$7-8 drawdown. User asked: for that recovering majority, how far do they actually peak once given the room to do so?
- **Test**: moved the post-BE loss cap out to **-$8** (matching the measured average drawdown), combined with the tight trail (10% of peak, no floor) already validated as the better trail shape. This is a real exit rule now, not a counterfactual — trades that would have recovered under the old -$1 cap can now actually survive long enough to reach the trail.
- No code changes needed — reuses `--trail-gap-pct`/`--trail-gap-floor-money`/`--post-be-loss-cap`, all already added to `scripts/backtest_exit_strategy.py`. Same window/gate as everything else.

## Results

| Entry | Config | Total P&L | Win rate | `trailing_breach_pct_of_peak` | `profit_drop_after_be` |
|---|---|---|---|---|---|
| SMA | Tight trail only ($1 cap) | -$37.00 | 28.8% | 116 (28.8%), avg +$2.78 | 250 (62.0%), avg -$1.14 |
| SMA | **Tight trail + $8 cap** | **-$47.40** | **66.1%** | **267 (65.6%), avg +$2.68** | 68 (16.7%), avg **-$8.28** |
| MACD | Tight trail only ($1 cap) | -$19.20 | 31.5% | 39 (31.5%), avg +$2.19 | 73 (58.9%), avg -$1.13 |
| MACD | **Tight trail + $8 cap** | **-$93.00** | **64.5%** | **79 (63.7%), avg +$1.98** | 25 (20.2%), avg **-$8.57** |

**Peak-bucket comparison** (trades reaching a peak > $2, where the trail is genuinely active):

| Entry | $1 cap: trades w/ peak>$2 | Captured by trail | $8 cap: trades w/ peak>$2 | Captured by trail |
|---|---|---|---|---|
| SMA | 126 | 108 (85.7%) | 279 | **267 (95.7%)** |
| MACD | 39 | 38 (97.4%) | 80 | **79 (98.8%)** |

## Findings

1. **Direct answer to "how far do the recovering trades peak": a lot further, and the trail now catches nearly all of them.** With room to survive, more than twice as many trades (SMA 279 vs 126, MACD 80 vs 39) develop a peak above $2, and the trail successfully captures 95-99% of them (up from 86-97%) at an average profit of **$2.68 (SMA) / $1.98 (MACD)** — implying an average underlying peak around **$2.98 (SMA) / $2.20 (MACD)**, since the 10% trail captures roughly 90% of peak.
2. **Win rate improves dramatically for both indicators** (SMA 28.8%→66.1%, MACD 31.5%→64.5%) — more than double.
3. **But total P&L gets WORSE for both, not better** — SMA -$37.00→-$47.40 (worse by $10.40), MACD -$19.20→-$93.00 (worse by $73.80, a large regression). This is the classic "more frequent small wins, fewer but much bigger losses" pattern, and here the bigger losses cost more than the extra wins gain. The trades that still don't recover even with $8 of room now cost 8x as much each (avg -$8.28/-$8.57 vs -$1.14/-$1.13), and that increase outweighs the trail's gains from capturing more, bigger wins.
4. **MACD is hit much harder than SMA.** This lines up with `post-cap-recovery-test.md`'s finding that MACD's failed-trades population was already less recoverable (78.1% vs SMA's 85.2%, deeper drawdown, longer recovery time) — moving the cap out gives MACD's "genuinely doomed" trades (not just the temporarily-underwater ones) much more room to rack up losses before finally being cut.
5. **This suggests the true optimum cap value is somewhere between $1 and $8, not at either endpoint tested so far.** Win rate climbing this fast while total P&L moves the wrong way is a strong signal that a smaller move (e.g. $2-4) might capture much of this recovery benefit without paying for as many of the "never was going to recover anyway" trades at a full $8 loss each. **Not yet tested** — a real sweep ($2/$3/$4/$5/$6/$7, same tight trail) is the natural next step, not a guess at $8 specifically.

## Caveats

- Single 4-week window, same as everything else this session.
- No live Config change — every configuration here is a backtest-only override.
