# Tight Trail Test — 10% of Peak, No Floor

Date: 2026-09-20

## Setup

- **Motivation**: `entry-excursion-trace.md` found real trades reach a substantial favorable peak (avg $4.73-6.12) 90-94% of the time, but the live post-BE trail — `gap = max($2.0 floor, 60% × peak)` (`ProfitExitManager._trail_gap`) — only ever locks in ~40% of that peak in its best case, and doesn't even activate at all for peaks under ~$3.33 (where the $2 floor forces `trigger ≤ 0`). User proposed tightening this dramatically: **gap = 10% of the maximum peak ever reached** (already how `best_profit` is tracked — a running max, never resets on a local pullback, so no code change was needed for the "maximum, not last peak" requirement), with the floor set to **$0** so the 10% isn't silently overridden by the $2 floor for smaller peaks.
- **New tooling**: `--trail-gap-pct` / `--trail-gap-floor-money` added to `scripts/backtest_exit_strategy.py`, overriding `ExitTradeConfig`'s existing `trail_gap_pct`/`trail_gap_floor_money` fields for one backtest run — no change to `Config` or any production default; `ProfitExitManager` itself is untouched.
- **Important caveat on the $0 floor**: with no floor, the trail's trigger (`peak - gap`) is positive — i.e. the trail is *live* — the instant any peak above $0 exists. This is a real whipsaw risk on trivial noise peaks that this test doesn't specifically isolate; the aggregate results below could be masking some fraction of premature exits on peaks so small they're barely more than spread/tick noise.
- Same 4-week window, same fixed pullback gate, same entry indicators (SMA/MACD) as every other full-lifecycle test this session. This only touches the post-BE trailing mechanism — the separate pre/post-BE loss cap (`EXIT_POST_BE_LOSS_CAP_MONEY=1.0`, governing `profit_drop_after_be`) is untouched and should show identical numbers before/after.

## Results

| Entry | Trail | Total P&L | Win rate | `profit_drop_after_be` | `trailing_breach_pct_of_peak` | `failed_to_reach_be` | `exhausted` |
|---|---|---|---|---|---|---|---|
| SMA | old (60%/$2) | -$91.60 | 28.8% | 250 (62.0%), avg -$1.14, tot -$284.20 | 108 (26.8%), avg +$1.71, tot +$184.60 | 33 (8.2%), avg -$1.56 | 8 (2.0%), avg +$10.37 |
| SMA | **new (10%/$0)** | **-$37.00** | 28.8% | 250 (62.0%), avg -$1.14, tot -$284.20 (**identical**) | 116 (28.8%), avg **+$2.78**, tot +$322.20 | 33 (8.2%), avg -$1.56 (**identical**) | **0** (absorbed into trail) |
| MACD | old (60%/$2) | -$21.60 | 31.5% | 73 (58.9%), avg -$1.13, tot -$82.80 | 38 (30.6%), avg +$1.63, tot +$61.80 | 12 (9.7%), avg -$1.83 | 1 (0.8%), avg +$21.40 |
| MACD | **new (10%/$0)** | **-$19.20** | 31.5% | 73 (58.9%), avg -$1.13, tot -$82.80 (**identical**) | 39 (31.5%), avg **+$2.19**, tot +$85.60 | 12 (9.7%), avg -$1.83 (**identical**) | **0** (absorbed into trail) |

## Findings

1. **The tighter trail helps — for both indicators, and the loss-cap mechanism is confirmed untouched.** `profit_drop_after_be`'s trade count, average, and total are bit-identical before/after for both SMA and MACD, exactly as expected: that outcome is governed by the separate pre/post-BE loss cap, not the trailing stop this change targets. Every dollar of improvement comes from the trailing-stop side.
2. **The improvement is not just "smaller average trail profit, more of them" — it's bigger average profit AND more trades captured, simultaneously.** `trailing_breach_pct_of_peak`'s average profit rose for both indicators (SMA: $1.71 → $2.78; MACD: $1.63 → $2.19), while also absorbing every previously-`exhausted` trade (SMA: 8 → 0; MACD: 1 → 0) into itself. The tighter trail is both catching trades earlier, before they've fully reversed, *and* no longer leaving trades running unresolved to the tick-budget cutoff — both effects point the same direction.
3. **But the improvement's size is very different between indicators**: SMA improves by **$54.60 (60% reduction in loss)**; MACD improves by only **$2.40 (11% reduction)**. On a per-trade basis this actually **flips the SMA-vs-MACD ranking yet again**: old trail had MACD ahead (-$0.174/trade vs SMA's -$0.227/trade); new trail has SMA clearly ahead (-$0.092/trade vs MACD's -$0.155/trade). This is now the *third* time in this investigation that which entry indicator "wins" has flipped depending on exactly how the exit strategy is configured — reinforcing that entry-indicator comparisons are not independent of exit-strategy calibration; they're entangled, and conclusions about one aren't stable without fixing the other.
4. **Both are still net-losing** (SMA -$37.00, MACD -$19.20) — the tighter trail materially reduces the loss but does not eliminate it, because `profit_drop_after_be` (the loss-cap side, ~59-62% of all trades, averaging -$1.13/-$1.14) is untouched and remains larger in total than what the improved trail now captures. This is consistent with `entry-excursion-trace.md`'s finding that the trail was previously capturing only a small fraction of real available peak profit — the trail side of the ledger clearly improved, but the loss-cap side is the next lever if this direction is pursued further (e.g., does a tighter or looser pre/post-BE loss cap pair better with this tighter trail than the current flat -$1.0?).

## Caveats

- Single 4-week window, same as everything else this session — the magnitude of improvement (especially SMA's dramatic 60% reduction) needs a second window before being trusted as durable rather than this month's specific price action.
- The $0 floor's whipsaw risk (flagged in Setup) isn't isolated here — it's possible some of the 116/39 `trailing_breach` trades are premature exits on noise-level peaks that a small floor would have avoided; this test can't distinguish "good tight trail" from "occasionally-too-eager tight trail" without a closer per-trade look or a floor sweep.
- Still a backtest-only override — `Config.EXIT_TRAIL_GAP_PCT`/`EXIT_TRAIL_GAP_FLOOR_MONEY` are untouched, no live behavior change.
