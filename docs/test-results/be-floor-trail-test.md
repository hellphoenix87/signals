# BE-Floor + Tight-Trail Test — A Clear Negative Result

Date: 2026-09-20

## Setup

- **User's proposal**: "if trailing stop is triggered at BE, but if the 10% buffer would be below BE, use BE as the exit threshold" — i.e. floor the worst-case post-breakeven outcome at breakeven itself, not a real loss.
- **Why this needed a cap override, not a trigger-flooring change**: a pure percentage trail (`gap = 10% × peak`, no floor) can never mathematically produce a trigger below breakeven, since `peak ≥ 0` implies `peak - 0.10×peak = 0.90×peak ≥ 0` always. The actual gap is elsewhere: `ProfitExitManager`'s trail only evaluates while `0 < profit < peak` — if price gaps straight through that (tiny, for small peaks) window into negative territory in a single tick, the trail's condition goes false and only the *separate* `LossExitManager` post-BE cap (currently -$1) catches it from there. So "floor the worst case at breakeven" means setting that cap to **$0**, not changing the trail's math.
- **Directly relevant context measured earlier this session**: breakeven arms almost instantly — median 1 tick, avg 4.4-5.2 ticks (`be_arm_ticks`, previous commit). Most trades arm on a single small favorable wobble, not a developed move.
- **New tooling**: `--post-be-loss-cap` added to `scripts/backtest_exit_strategy.py`, overriding `ExitTradeConfig.post_be_loss_cap_money` — backtest-only, no Config change. Tested combined with the tight trail (`--trail-gap-pct 0.10 --trail-gap-floor-money 0.0 --post-be-loss-cap 0.0`), same window/gate as everything else this session.

## Results

| Entry | Config | Total P&L | Win rate | `profit_drop_after_be` | `trailing_breach_pct_of_peak` |
|---|---|---|---|---|---|
| SMA | Original (60%/$2, $1 cap) | -$91.60 | 28.8% | 250 (62.0%), avg -$1.14, avg 183.8 ticks | 108 (26.8%), avg +$1.71 |
| SMA | Tight trail only (10%/$0, $1 cap) | -$37.00 | 28.8% | 250 (62.0%), avg -$1.14, avg 183.8 ticks (unchanged) | 116 (28.8%), avg +$2.78 |
| SMA | **+ BE-floor cap (10%/$0, $0 cap)** | **-$109.00** | **4.0%** | 350 (86.6%), avg **-$0.22**, avg **21.1 ticks** | **16 (4.0%)**, avg +$3.07 |
| MACD | Original (60%/$2, $1 cap) | -$21.60 | 31.5% | 73 (58.9%), avg -$1.13, avg 222.9 ticks | 38 (30.6%), avg +$1.63 |
| MACD | Tight trail only (10%/$0, $1 cap) | -$19.20 | 31.5% | 73 (58.9%), avg -$1.13, avg 222.9 ticks (unchanged) | 39 (31.5%), avg +$2.19 |
| MACD | **+ BE-floor cap (10%/$0, $0 cap)** | **-$32.20** | **6.5%** | 104 (83.9%), avg **-$0.22**, avg **14.4 ticks** | **8 (6.5%)**, avg +$1.62 |

## Findings

1. **This is a clear negative result for both indicators.** Adding the $0 post-BE cap made things *worse* than either prior config — SMA -$109.00 (vs -$37.00 tight-trail-only, vs -$91.60 original); MACD -$32.20 (vs -$19.20, vs -$21.60). Win rate collapsed dramatically (SMA 28.8%→4.0%, MACD 31.5%→6.5%).
2. **The mechanism, confirmed by the data**: with the cap at $0, `profit_drop_after_be` fires the instant profit touches $0 or below, post-BE — and since BE itself arms on a median of 1 tick (often just a tiny favorable wobble, not a real move), ordinary bid/ask noise right after arming now counts as a hard exit signal. Average resolution time for that outcome collapsed from 183.8/222.9 ticks down to 21.1/14.4 ticks — trades are being cut almost instantly, before they have any chance to develop.
3. **Per-trade loss size did shrink dramatically** (-$1.14 → -$0.22 SMA; -$1.13 → -$0.22 MACD) — the cap change did exactly what it was supposed to do for the trades it caught. But it caught vastly more trades doing it (SMA 250→350, MACD 73→104) at the direct expense of `trailing_breach_pct_of_peak` (SMA 116→16, MACD 39→8) — the trades that used to survive long enough to reach a real $2+ peak and get captured by the trail mostly no longer get the chance to. The $1 cap, despite allowing a real per-trade loss, was doing useful work: buying trades room to survive past initial post-arming noise long enough to develop into the moves the trail depends on. Removing that room didn't protect the downside in net terms — it destroyed far more upside than it saved.
4. **This rules out the naive "floor exactly at breakeven" implementation** — at least via a literal $0 cap. It doesn't rule out a cap somewhere between $0 and $1 (untested), or a design that only tightens the cap after some minimum post-BE survival time/peak has been reached rather than from the first tick BE arms.

## Caveats

- Single 4-week window, same as everything else this session — though the size and direction of this regression (worse on every measure, for both indicators) is large enough that it's unlikely to be pure noise, unlike some of the more marginal findings elsewhere in this investigation.
- No live Config change made or considered — this result argues clearly against making one in this direction.
