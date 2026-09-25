# STF Session-Filter Inversion (Test M)

Date: 2026-09-25. Pre-registered in [pre-registrations-2026-09-22.md](pre-registrations-2026-09-22.md)
(Test M) before any screen-window data was fetched.

**TL;DR**: For single-timeframe (STF, live since PR #73), the live session filter blocks the
*better* trades. Trading only 08:00-18:59 UTC (the hours it currently blocks) beats the shipped
filter on per-trade expectancy in **8 of 8** graded windows, pooled -$0.648 -> **-$0.509/trade**
(+$0.139), and cuts total loss **28%** (-$17,216 -> -$12,313). **Every arm still loses in every
window** -- this is "lose less", not an edge. Stage status: see [Stages](#stages).

## Why the filter exists, and why it doesn't transfer

The 08-18 UTC block was derived for **MTF**, whose bias/confirm gates get whipsawed during the
London/NY session ([session-filter-analysis.md](session-filter-analysis.md)). That analysis also
checked STF, with a fixed 5/5 target/stop proxy, and found **no** effect. Under the real exit
system (breakeven arming, $1 post-BE cap, staircase trail) what matters is *movement relative to
spread*: London/NY has the tightest spreads and the most movement, so trades reach breakeven more
often. The proxy could not see that; the full lifecycle does.

## Setup

- Production code only: `scripts/backtest_exit_strategy.py --full-lifecycle --single-timeframe
  --full-lifecycle-max-ticks 10000 --max-entry-spread-pips 1.0` (real `strategy_factory` + real
  `ExitTrade`, live config: staircase on, arm=30, $1 post-BE cap, $5 pre-BE SL, 1.0p entry gate),
  with `Config.USE_SESSION_FILTER` toggled off.
- **One unfiltered run per window yields every arm.** Trades are simulated independently, so the
  filter-on run equals the open-hours subset of the filter-off run -- verified on W3
  trade-for-trade (3,550 = 3,550, identical outcomes and dollars).
- Arms: **SHIPPED** trades 19:00-07:59 UTC; **INVERTED** trades 08:00-18:59 UTC; **OFF** all hours.
- UTC tagging uses the production conversion (`SessionFilteredSignalStrategy._utc_hour` with
  `BROKER_TIMEZONE="Europe/Athens"`): candle frame = UTC+3 in winter, UTC+5 in summer. See
  [the DST finding](#side-finding-the-filter-was-2h-off-in-winter).
- Windows pinned by date (canonical W-numbers).

## Hypothesis source (W3, excluded from grading)

| Arm | Trades | Total | $/trade | Win % |
|---|---|---|---|---|
| SHIPPED (19-07) | 3,550 | -$724.60 | -0.204 | 30.1 |
| INVERTED (08-18) | 3,050 | -$297.20 | **-0.097** | 32.5 |
| OFF | 6,600 | -$1,021.80 | -0.155 | 31.2 |

## Results

| Window | SHIPPED $/tr | INVERTED $/tr | Delta | SHIPPED total | INVERTED total |
|---|---|---|---|---|---|
| W6 (worst) | -1.011 | -1.004 | +0.007 | -$3,040 | -$2,942 |
| W5 | -0.802 | -0.732 | +0.070 | -$2,582 | -$1,974 |
| W7 | -0.770 | -0.642 | +0.128 | -$2,646 | -$1,970 |
| W2 (calm) | -0.123 | -0.061 | +0.062 | -$462 | -$199 |
| W10 | -0.700 | -0.427 | +0.273 | -$2,334 | -$1,222 |
| W8 | -0.687 | -0.436 | +0.251 | -$2,494 | -$1,321 |
| W9 | -0.610 | -0.404 | +0.206 | -$2,240 | -$1,334 |
| W12 | -0.565 | -0.448 | +0.117 | -$1,419 | -$1,351 |
| **Pooled (8)** | **-0.648** | **-0.509** | **+0.139** | **-$17,216** | **-$12,313** |

Trade counts: SHIPPED 26,586, INVERTED 24,181 (9% fewer), OFF 50,767.

### Screen (W6/W5/W7/W2) -- PASS

(a) INVERTED per-trade better in 4/4 (needed 3); (b) pooled per-trade -0.650 -> -0.592;
(c) pooled total -$8,730 -> -$7,085. Welch t = 2.7 on ~25k trades; INVERTED better on 55 of 80
trading days.

### Expand 1 (+W10/W8) -- PASS

Bar fixed before grading (not in the original pre-registration, stated before the numbers were
read): cumulative >= 5 of 6 windows, pooled per-trade and pooled total both better. Result: 6/6,
and the two expansion windows show the **largest** gains of any window (+$0.27, +$0.25).

### Expand 2 (+W9/W12) -- PASS

Bar stated before grading: cumulative >= 6 of 8, pooled per-trade and total both better. Result:
**8/8**. W12 straddles the 26 Oct 2025 DST change (handled by the production conversion). In W12
INVERTED traded **more** than SHIPPED (3,015 vs 2,510) and still lost less -- the gain is not an
artifact of trading less.

## Mechanism

Screen windows pooled, by exit reason:

| | SHIPPED | INVERTED |
|---|---|---|
| `failed_to_reach_be` (30-tick timeout) | 28.4% of trades | **24.3%** |
| staircase trail exit | 21.6% @ +$2.13 | **23.2%** @ +$2.18 |
| `profit_drop_after_be` ($1 cap) | 49.3% @ -$1.24 | 51.7% @ -$1.24 |

The gain is pre-breakeven: London/NY trades arm breakeven more often within 30 ticks, and more of
them go on to the trail. Post-BE behaviour (cap cost per hit) is unchanged.

## Caveats

- **Still net negative everywhere.** Inversion is the best filter found, not a profitable system.
- **W6 is a tie** (+$0.007). The gain is not uniform; it is largest in winter windows (W10, W8).
- **Not tested: which of the 24 hours matter.** Only the pre-registered 11-hour block, flipped.
  Single hours are unstable (session-filter-analysis.md, Finding 4) -- do not hand-pick hours.
- **Fewer trades** (-9% pooled) accounts for part of the total-loss reduction, but per-trade
  improves in every window, and W12 improves with *more* trades.

## Side finding: the filter was 2h off in winter

The broker's server clock follows **EU** DST (UTC+2/+3): week-close bars shift Sat 00:59 -> 01:59
at 29 Mar 2026, not at the US 8 Mar change, and the spread-gated rollover lands at 21:00 UTC
(= 17:00 NY) under +5. The filter used one offset measured at startup (+5 today), so:

- a bot left running past **25 Oct 2026** would block the wrong hours by 2h until restarted;
- every filter-on backtest of a winter window (W8-W12, W7 before 29 Mar) actually blocked 06-16
  UTC, not 08-18. Earlier A/B comparisons still hold (both arms saw the same trades), but those
  windows were not filtering the session they claimed to.

Fixed on this branch: `Config.BROKER_TIMEZONE` + per-timestamp conversion. Summer behaviour is
unchanged; the grader above uses the same production conversion and reproduces the hand-tagged
numbers to the cent.

## Stages

| Stage | Windows | Result |
|---|---|---|
| Screen | W6, W5, W7, W2 | PASS (4/4) |
| Expand 1 | + W10, W8 | PASS (6/6) |
| Expand 2 | + W9, W12 | PASS (8/8) |
| Full | + W4, W1 (W3 excluded: hypothesis source) | running |

## Shipping (not done -- user decision)

Config only, no code: `SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL["EURUSD"] =
list(range(19, 24)) + list(range(0, 8))`. Depends on the DST fix above being merged first, or the
block would drift 2h in winter.
