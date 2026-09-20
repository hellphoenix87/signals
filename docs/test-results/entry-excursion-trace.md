# Entry Excursion Trace — Continuous Real-Tick Tracking From Entry

Date: 2026-09-20

## Setup

- **Motivation**: user's methodological critique of `--quick-check` — it took three *independent, disconnected* fixed-horizon snapshots (1/5/15 M1 bars) using bar high/low extremes, not a continuous trace of one trade's actual real-tick progression, and capped out at 15 minutes when real trades run far longer (full-lifecycle's `trailing_breach` winners averaged 850-1050 *ticks*, not minutes).
- **New tooling**: `measure_entry_excursion()` in `scripts/backtest_exit_strategy.py` (`--measure-entry-excursion`), mirroring the existing `--measure-post-be` tool's methodology (real tick stream, no exit rule applied, running-peak/drawdown-from-peak tracking) but starting at **tick 0 (the moment of entry)** instead of at breakeven arming — so it also covers whatever happens *before* breakeven, which `--measure-post-be` never sees. One continuous pass per trade, up to a 4,000-tick budget, with zero exit logic of any kind (not even the pre-BE soft-SL) — pure observation of what real price does after this specific entry.
- Confirms, per a separate clarification in this session: every signal counted here already passed through the real `MultiTimeframeStrongSignalStrategy`'s weighted bias(0.5)/confirm(0.3)/entry(0.2) scoring plus its bias-vs-entry hard veto — this was never a single indicator tested in isolation, only the M1 entry layer's indicator was swapped.
- Same 4-week window as every other test this session, both entry indicators (SMA — today's `Config.MTF_ENTRY_INDICATOR` — and MACD).

## Results

| Entry | Signals | Ever favorable | Still favorable at cutoff | Avg peak | Avg ticks to peak | Avg drawdown from peak | Avg final (mark-to-market) | Avg % of peak retained |
|---|---|---|---|---|---|---|---|---|
| SMA | 403 | 93.8% | 50.1% | **+$6.12** | 1,757 | $9.39 | -$0.14 | **32.2%** |
| MACD | 124 | 90.3% | 41.1% | **+$4.73** | 1,565 | $9.88 | -$1.97 | **25.9%** |

(At `lot=0.2` EURUSD, ~$2/pip: SMA's average peak is roughly 3 pips, MACD's roughly 2.4 pips.)

## Findings

1. **Real, often substantial favorable moves happen on the large majority of trades** — 90-94% ever reach a positive peak, averaging $4.73-$6.12 (roughly 2.4-3 pips). This is a stronger, more direct version of `quick-check-current-config.md`'s finding (83-96% had *some* favorable moment within 15 minutes, averaging +0.4 to +2.4 pips) — this trace watches much longer (up to 4,000 ticks vs. 15 M1 bars) and finds correspondingly bigger average peaks, consistent with that earlier result's still-rising trend at the 15-minute mark rather than contradicting it.
2. **The large majority of that peak is given back by the time observation stops**: only 26-32% of the peak survives on average. This is the starkest, most direct evidence yet for why `full-lifecycle-entry-indicator-comparison.md`'s `profit_drop_after_be` outcome (~60% of trades reach breakeven, then reverse and get cut for a small loss) dominates the real P&L — there is real profit on the table (multiple dollars, not fractions of a cent), and the current exit mechanism is capturing only a sliver of it. The live trailing-stop winners in the full-lifecycle test averaged just +$1.6-1.7 — a small fraction of the $4.73-6.12 peaks measured here.
3. **This further updates (and strengthens) the correction to `invert-signal-test.md`'s "near-zero directional information" framing.** A $4-6 average favorable peak reached by 90%+ of trades is a strong, direct signal that the entry has real value. The money-losing mechanism is squarely on the exit-strategy-calibration side: the post-BE trail gap and loss cap are sized far too small relative to what a typical trade's real favorable excursion actually looks like.
4. **Timing detail worth flagging for exit-strategy work specifically**: average ticks-to-peak (1,565-1,757) is *longer* than the full-lifecycle `trailing_breach_pct_of_peak` outcome's average resolution time (850-1,050 ticks). That means the current live exit mechanism, which reacts to whatever the running peak-so-far is at any given moment, often locks in a trade's outcome (via the cap or an early trail trigger) *before* the trade's true, larger peak has even occurred. This points at the exit strategy potentially needing more time/room to let a position develop, not just a differently-sized dollar threshold.
5. **SMA outperforms MACD on every metric in this trace** (bigger peak, more retained, better final mark-to-market: -$0.14 vs -$1.97) — but note this doesn't match the full-lifecycle P&L ranking (where MACD lost less than SMA, -$21.60 vs -$91.60). That's not a contradiction: full-lifecycle applies real exit rules that terminate trades at economically meaningful points (a cap hit, a trail breach), while this trace runs everything to an arbitrary 4,000-tick cutoff with no intervention at all — a fundamentally different stopping condition. These two tools answer different questions and aren't directly comparable on final P&L; the entry-excursion numbers are informative about *available opportunity* (peak, retention), not about what any specific exit rule would have captured.

## Caveats

- Single 4-week window, same as everything else this session.
- The "avg final (mark-to-market)" and "% still favorable at cutoff" numbers describe a fictional stopping point (4,000 ticks with no exit rule) — many of these trades are still nominally "open" at that point and could move either way afterward. They are not real closed outcomes; treat them as descriptive of the path so far, not as a P&L claim.
- The 32.2%/25.9% "retained" figures are themselves a snapshot at that same arbitrary cutoff — they say "this much of the peak survived to this particular stopping point," not "this is what a real trader could have captured," since capturing anything requires an actual exit rule, which is exactly the calibration question this points toward, not something this tool answers by itself.

## Bottom line

Tracing a real trade continuously from entry, with nothing intervening, it does progress favorably — substantially, and most of the time. It just doesn't hold onto that gain by any fixed later point we choose to look at. That is now the most direct evidence in this whole investigation that the entry signal itself is not the problem; the exit strategy's specific trail/cap/timing parameters, calibrated against real favorable-move sizes that are several times larger than what's currently being captured, are the more promising thing to fix next.
