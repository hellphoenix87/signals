# Staircase Trail: W3/W4 Confirmation

**Date**: 2026-09-24

**TL;DR**: The `$2` staircase trail beats the live percentage-of-peak trail on W3 and W4, closing
the open item left by
[exit-strategy-reactive-and-staircase-investigation.md](exit-strategy-reactive-and-staircase-investigation.md).
That makes it **4/4 windows**, and it was wired into `ProfitExitManager`
(`EXIT_STAIRCASE_TRAIL_ENABLED = True`). **The system remains net negative on every window** --
this reduces bleed, it does not make the bot profitable.

## Why this run existed

The original investigation found the staircase beat the live trail on W1-W2 but gated it
explicitly:

> **Only validated on W1-W2**; confirm on W3/W4 at the live cap before relying on it.

That gate mattered. In the same investigation the wide/disabled post-BE cap looked excellent on
W1-W2 (+$1,124 / +$3,209) and reversed hard on exactly W3/W4 (-$595 / -$1,350). Confirming before
shipping was the whole point.

## Setup

`scripts/backtest_exit_strategy.py --full-lifecycle --single-timeframe
--full-lifecycle-max-ticks 10000`, live `$1` post-BE cap, `$5` pre-BE soft SL, 30-tick arming
window. Baseline arm is production as it stood at `a925239`; staircase arm adds
`--staircase-trail --staircase-tier-width 2.0`.

Windows are **pinned by date** (`--start-date`/`--end-date`), not `--start-pos`, so the two arms of
each pair replay exactly the same trades. W4 is a **full 4 weeks** here, unlike the investigation's
partial Jun 15-30 -- the MT5 M1 history limit that forced that has since lifted (history now
reaches back to 2025-05-22).

- **W3** = 2026-06-30 -> 2026-07-28 (28,799 M1 candles, 3,625 trades)
- **W4** = 2026-06-02 -> 2026-06-30 (28,799 M1 candles, 3,827 trades)

## Result

| Window | Baseline | Staircase | Delta |
|---|---|---|---|
| W3 | -$1,499.40 (win 27.5%) | **-$1,118.60** (win 29.6%) | **+$380.80** |
| W4 | -$3,023.40 (win 20.7%) | **-$2,783.80** (win 22.7%) | **+$239.60** |

### W3 by exit reason

| Reason | Baseline | Staircase |
|---|---|---|
| `profit_drop_after_be` (post-BE $1 cap) | 2411 (66.5%) -$2,894.80 | 2335 (64.4%) -$2,787.40 |
| trail breach | 969 (26.7%) **+$1,473.80** @ +$1.52, 1,140 ticks | 1074 (29.6%) **+$2,216.80** @ +$2.06, 323 ticks |
| `failed_to_reach_be` (30-tick timeout) | 190 (5.2%) -$214.00 | 190 (5.2%) -$214.00 |
| `profit_drop` (pre-BE $5 soft SL) | 26 (0.7%) -$334.00 | 26 (0.7%) -$334.00 |
| `exhausted` (still open at cutoff) | 29 (0.8%) +$469.60 | **0** |

### W4 by exit reason

| Reason | Baseline | Staircase |
|---|---|---|
| `profit_drop_after_be` | 2182 (57.0%) -$2,693.40 | 2104 (55.0%) -$2,590.80 |
| trail breach | 776 (20.3%) **+$1,286.40** @ +$1.66, 827 ticks | 869 (22.7%) **+$1,787.20** @ +$2.06, 193 ticks |
| `failed_to_reach_be` | 742 (19.4%) -$1,166.40 | 742 (19.4%) -$1,166.40 |
| `profit_drop` | 112 (2.9%) -$813.80 | 112 (2.9%) -$813.80 |
| `exhausted` | 15 (0.4%) +$363.80 | **0** |

## Three things that make this more than a headline number

**The comparison is provably clean.** `failed_to_reach_be` and `profit_drop` are identical *to the
dollar* across both arms in both windows. Those are the pre-breakeven exits, which the staircase
does not touch -- so the change is isolated to the post-BE trail, as designed.

**The delta is understated.** The baseline's `exhausted` bucket (trades still open when the 10k-tick
budget runs out, marked at an arbitrary price) is credited +$469.60 / +$363.80. The investigation
flags that bucket as **systematically optimistic** (avg +$10-16/trade). The staircase eliminates it
entirely -- it always reaches a real exit. Excluding that phantom credit from the baseline for a
like-for-like read: **W3 +$850.40, W4 +$603.40**.

**The mechanism matches the claim.** The staircase captures *more* trades (1,074 vs 969; 869 vs
776) at a *higher* average (+$2.06 vs +$1.52/+$1.66) in a *quarter* of the time (323 vs 1,140
ticks; 193 vs 827). In W3 it converts essentially the whole `peak > $2` population into trail exits
(1,074 of 1,076) where the baseline let 78 of them fall through to the cap.

## What this does not fix -- read before celebrating

**Every window is still net negative**, on all four now tested.

**The post-BE `$1` cap is still where the money goes**: -$2,787 (W3) and -$2,591 (W4) *with* the
staircase. The trail was already the net-positive component; this makes a profitable part more
profitable.

**The staircase cannot see most of the book.** It is inactive below a `$2` peak, exactly like the
trail it replaces. In W3, of 3,409 trades that reached break-even:

| Post-BE peak | Count | Decided by |
|---|---|---|
| <= $1 | 1,713 (50%) | cap only |
| $1 - $2 | 620 (18%) | cap only -- trail structurally inactive |
| > $2 | 1,076 (32%) | the trail |

So 68% of break-even-reaching trades never interact with the trail at all.

One diagnostic worth keeping in view: of W3's 2,411 cap exits, **95.6% would have recovered to
break-even or better** had the cap not fired -- but **99.1% of those then reversed below zero
again** (avg peak before that second reversal: $7.67). The cap is cutting mostly-recoverable
positions, and "recoverable" is not "holdable". That is very likely why widening the cap blew up on
W3/W4 previously.

## Follow-up

Retune `EXIT_POST_BE_LOSS_CAP_MONEY` against the *new* trail shape. The cap was tuned against the
percentage-of-peak trail; the staircase changes which trades survive to meet it.
`scripts/backtest_exit_strategy.py --sweep-staircase-cap` exists for exactly this. Given the
numbers above, the cap is the more promising lever -- but it carries its own validation burden, and
the wide-cap history says treat any single-window win with suspicion.

This is backtest evidence only. No live run has yet produced a `staircase_trail_breach` -- see
item 6 of [../tech-debt.md](../tech-debt.md).
