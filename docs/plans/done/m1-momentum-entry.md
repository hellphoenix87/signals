# M1 momentum entry: trigger + strength filter + context

Status: done 2026-09-26 -- nothing passed Phase 3; Phase 4 not started (branch `m1-momentum-entry`)

## Goal

Find an M1 entry that catches moves which keep going, so the existing staircase trail can ride them.
The live exits stay as they are ($1 post-BE cap, $2 staircase, 15 s zero-spread wait, 0.1-pip gate).
The system breaks even when **34.8%** of trades reach the staircase (+1 pip before -0.5 pip); the live
MACD entry reaches **33.2-33.5%**, i.e. random-walk level. A momentum entry has to beat that by
~+3 pp, consistently.

Approach agreed with the user: **one trigger + one strength filter + the right context**, at most two
indicators, each added only if it beats the simpler version without it. No stacking of indicators that
all read direction from the same prices.

## Out of scope

- Exit changes (cap, staircase, arming) -- exit tuning is exhausted
  (docs/test-results/exit-tuning-limits-and-entry-focus.md).
- RSI / MACD / Stochastic / Bollinger / SMA-crossover variants as the main trigger -- already tested.
- The M5 RSI-fade system -- abandoned by the user (too slow, too fragile); do not revive it.
- Multi-timeframe gating as in the old MTF strategy (cost 30x the sample, picked worse trades).

## Context the next session needs

What the evidence says (details: docs/test-results/entry-timing-and-rsi-fade-m5.md):

- At M1, in the current live hours (19:00-07:59 UTC), **every momentum rule screened was worse than a
  random direction**: SMA trend -0.5..-0.7 pp, 60/240-bar trend -0.4..-0.5, follow-last-candles
  -0.7, Donchian 20/60 breakout -1.1..-1.4, RSI/Bollinger follow -1.6..-1.8. Their mean-reversion
  mirrors were all positive.
- Indicator values at entry do not predict reaching the staircase (AUC 0.50).
- Plain breakouts failed; the untested ingredient is **volatility expansion + trend strength**.
- Momentum rules were only screened at the live +1/-0.5 pip geometry, and only in the live hours.
  **London/NY hours (08-18 UTC) -- blocked by the live session filter -- were never screened for
  momentum.** Test M (old entry) found those hours better per trade in 10/10 windows; with the
  zero-spread wait the difference was inconclusive.

Data and tooling:

- **Spent for entry-signal hypotheses: all tick data from 2021-03 onward.** Screens on it are
  exploratory only. Confirmation needs data from before 2021-03 (check availability first -- W72,
  2021-03-16, still had ticks but a wide-spread month) or a demo forward run.
- Screening windows (spent, fine for exploration): W1 2026-08-25, W2 2026-07-28, W31-W42
  (2023-07-04 .. 2024-05-07, 28-day windows).
- **The signal lab (`scripts/signal_lab.py`) was not merged.** Restore it from closed PR #79:
  `git fetch origin refs/pull/79/head:pr79 && git checkout pr79 -- scripts/signal_lab.py`
  (commit a44cf6b). It scores any rule's entries against the same-tick random direction with the
  live entry (zero-spread wait) and the +1/-0.5 pip barrier; 95.8% agreement with the full backtest.
- Production backtests: `scripts/backtest_exit_strategy.py --full-lifecycle --single-timeframe`;
  M1 ~10 min per window with the session filter on; max two concurrent MT5 streams.

## Metrics and bar (fixed now, before any screen)

- Screen metric: win % in the +1/-0.5 pip barrier game, and **edge = win % minus the same-tick
  random-direction win %**. Also report trades per window and staircase-reach share.
- A layer passes when: pooled edge >= +3 pp, positive in >= 10/12 windows with >= 100 trades, better
  than the live MACD's edge (+1.32 pp) in the same hours, **and better than the previous, simpler
  layer**. Report the result of every configuration tried, not only the best.
- Indicator parameters are the standard defaults listed below, fixed before running -- no tuning on
  the screening windows.

## Phases

### Phase 1: Tooling

#### Subphase 1.1: Restore the signal lab
- Change: restore `scripts/signal_lab.py` from PR #79 (command above).
- Acceptance: `PYTHONPATH=. pipenv run python scripts/signal_lab.py` reproduces the known table
  (e.g. live MACD +1.32 pp, Donchian-60 follow -1.37 pp).

#### Subphase 1.2: Session-hours option
- Change: let the lab build windows for a chosen hour set -- `live` (19-07 UTC, current), `ldn_ny`
  (08-18 UTC), `all` -- using the production DST-correct UTC conversion. The live MACD baseline
  exists only for the live hours (it came from recorded runs); for other hours compute it with the
  production strategy per candle (the lab already has `production_macd_directions`).
- Acceptance: `live` numbers unchanged; `ldn_ny` builds without errors and reports its own random-
  direction baseline.

### Phase 2: Indicators (pandas, from candles up to and including the signal candle)

Standard parameters, fixed:
- **Keltner breakout**: EMA(20) +/- 2 x ATR(10); close above upper -> buy, below lower -> sell.
- **ADX rising**: ADX(14) above its value 3 bars earlier and ADX >= 20; direction from +DI vs -DI.
- **Supertrend(10, 3)**: signal on a trend flip.
- **KAMA(10, 2, 30)**: slope sign over the last 3 bars; efficiency ratio(10) as strength.
- **Hull MA(21)**: slope sign.
- **Aroon(25)**: Aroon-up crossing above Aroon-down (and reverse).
- **ROC(10)** beyond +/-1 x its own 100-bar std.
- **Volatility expansion**: ATR(10) / ATR(100) >= 1.3.
- (Optional, later) tick-count surge in the last 60 s before the candle close.

- Acceptance: each indicator unit-checked by eye on a short synthetic series (direction, no
  lookahead: value at candle i uses candles <= i only).

### Phase 3: Layered screen (spent windows, exploratory)

- 3.1 **Triggers alone** -- Keltner breakout, Supertrend flip, KAMA slope, Hull slope, Aroon cross,
  ROC -- in `live`, `ldn_ny` and `all` hours.
- 3.2 **Best 1-2 triggers + one strength filter** -- ADX rising, KAMA efficiency ratio >= 0.3, or
  volatility expansion.
- 3.3 **+ context** -- session hours if not already decisive; volatility expansion if not used in 3.2.
- Each step records every configuration; a layer is kept only if it passes the bar above AND beats
  the previous layer.
- Acceptance: a table of all configurations with edge, windows positive, trades; a written verdict.

### Phase 4: Only if a configuration passes Phase 3

- Pre-register it (rule, parameters, hours, bar) in `docs/test-results/pre-registrations-2026-09-22.md`.
- Confirmation data: pre-2021-03 ticks if available (check the spread regime first, without looking at
  outcomes), otherwise a demo forward run.
- Production build behind Config flags (off by default): an entry-strategy switch like the removed
  `ENTRY_STRATEGY`, keeping the live exits and spread wait; session hours via
  `SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL`. Full-lifecycle production backtest for the test.
- If nothing passes Phase 3: write it up and stop -- M1 momentum with simple indicators does not beat
  random in this data.

### Phase 5: Write-up and PR (the user merges)

## Open questions

- If the best result needs London/NY hours: shipping it means changing the live session filter,
  which today blocks exactly those hours. User decision at that point.
- Pre-2021 tick availability and spread regime -- check at the start of Phase 4, not before.

## QA

- Phase 1: lab restored; the live-hours table reproduces the known numbers (boll_fade +1.80, Donchian-60
  follow -1.37). Production MACD computed per candle: +1.33 pp live (recorded run: +1.32), -0.16 pp ldn_ny.
- Phase 2: indicators checked on a synthetic up/flat/down series (they point the right way, flips and
  crosses fire only at turns) and for lookahead (0 mismatches when the series is cut at i).
- Phase 3: no configuration passes. The best is KAMA slope + vol expansion in ldn_ny at +1.14 pp (11/13),
  with win 33.81% < 34.8% break-even. Every trigger is negative in live hours. Full tables:
  docs/test-results/m1-momentum-entry-screen.md.
- Phase 4: skipped, as the plan says to when nothing passes.

