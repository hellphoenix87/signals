# Pre-registration — out-of-sample test on newly unlocked history (written 2026-09-22, before any run)

Setup (identical to W1–W4): EURUSD, `--full-lifecycle --single-timeframe --full-lifecycle-max-ticks 10000`, live `$1` post-BE cap.
Windows (never used for tuning): W4 full re-run (86401) + W5..W12 = start-pos 115201, 144001, 172801, 201601, 230401, 259201, 288001, 316801.

## Test A — `$2` staircase vs live percentage trail
- Variant: `--staircase-trail --staircase-tier-width 2.0`. Baseline: no flag.
- PASS if staircase total > baseline total in at least 7 of the 9 windows AND summed over all 9.

## Test B — regime rules (tag only, fixed thresholds, applied to baseline per-trade CSVs)
- B1: H1 Kaufman ER(20) >= 0.35 = trending, else ranging (most recent closed H1 bar before entry).
- B2: D1 ER(20) >= 0.35 = trending, else ranging.
- B3: M1 ATR at entry >= 0.71 pips = "not calm" (the rule that failed W4).
- A rule PASSES only if the per-trade avg P&L difference between its two states has the same sign in at least 8 of 9 windows.
- No new thresholds or rules will be added after seeing results; any new idea gets its own pre-registration and a fresh set of windows.

## Test C — does the entry signal have a directional edge in the year-old windows? (added 2026-09-22, before running)
- Run `--invert-signal` on the same 9 windows, same exits (pct trail, $1 cap, 10k ticks).
- edge = normal_total - inverted_total, per window. In W1-W3 this was +$740/+$616/+$886.
- PASS (edge is real) if edge > 0 in at least 8 of 9 windows.
- If it FAILS, the entry signal carries no usable direction information in that era and no exit tuning can fix it.

## Test D — how far does price actually move our way after entry?
- `--measure-entry-excursion` on 3 year-old windows (86401, 201601, 316801) + 1 recent (57601) as control.
- Descriptive, no pass/fail: compare max favourable excursion (MFE) and max adverse excursion (MAE)
  before the pre-BE stop would fire, recent vs year-old.
- Purpose: separate "price never goes our way" (signal problem) from "price goes our way then we mishandle it" (exit problem).

## Test F — breakeven arming timeout (written before running)
- New flag `--be-arming-ticks N` (full-lifecycle override; 0 = no timeout).
- Rationale: ~89% of trades reach their unconstrained peak AFTER tick 90; for trades that
  died pre-BE, the best price within the first 90 ticks was still NEGATIVE (-$0.46 to -$1.27),
  so a wider stop cannot save them -- the arming wall closes them regardless.
- Stage 1 screen: windows 86401 (W4), 201601 (W8), 1 (W1), 57601 (W3) at N = 300 and N = 0,
  vs the existing N=90 baseline.
- Proceed to all 12 windows ONLY if some N beats baseline in >= 3 of the 4 screen windows.
- Stage 2 PASS: beats baseline in >= 9 of 12 windows AND on total.
- If Stage 1 fails: record and stop. No sweeping N to find a winner.
- Report avg loss per losing trade alongside totals: holding longer converts small
  losses into large ones, which is a risk-profile change even if totals improve.

## Test G — MTF entry indicator (written before running)
- Live config uses MTF_ENTRY_INDICATOR="macd". Forward-move PoC (4 windows, 5/15/30/60 min)
  ranked MACD and SMA as ANTI-predictive (hit rate 47.9% / 46.6%, negative mean forward move
  at every horizon), RSI positive (52.1%).
- Baseline: the in-flight MTF runs (Config default = macd), all 12 windows, full-lifecycle, 10k ticks.
- Variants: --mtf-entry-indicator rsi, and sma, same 12 windows.
- PASS if a variant beats macd in >= 9 of 12 windows AND on total P&L.
- Bollinger/Stochastic are NOT tested here: a prior session found they produce ~0 MTF-gated
  signals (0 and 2/8), so under MTF gating there is nothing to measure. They belong in a
  separate single-timeframe test (Test H) where they actually fire.

## Test H — Bollinger / Stochastic as the SINGLE-TIMEFRAME entry (written before running)
- Prior session ruled both out, but only because MTF gating left them ~0 signals. In STF they
  fire freely (PoC: bollinger ~1,900/window, stochastic ~7,200/window vs rsi ~3,900).
- Forward-move PoC ranked them 1st and 2nd of five: bollinger +0.151 pips @15min (52.8% hit),
  stochastic +0.143 @60min (51.9%), vs rsi +0.072/+0.113 (52.1%), macd and sma both negative.
- Baseline: the existing STF runs (macd+sma+rsi vote, which is 98.4% RSI-alone by construction).
  Caveat: not identical to a pure --indicators rsi run; the 1.6% non-RSI trades differ.
- Stage 1 screen: windows 1 (W1), 57601 (W3), 201601 (W8), 316801 (W12), with
  --single-timeframe --indicators bollinger, and --indicators stochastic.
- Proceed to all 12 windows ONLY if a variant beats baseline in >= 3 of the 4 screen windows.
- Stage 2 PASS: >= 9 of 12 windows AND on total.
- Report trades/window alongside P&L: stochastic fires ~2x more than rsi, so equal total P&L
  means worse per-trade expectancy and more cost exposure.

## Test I — TIGHTENING pre-BE (the inverse of Tests D/F, written before running)
- Rationale: widening in distance (ATR-normalize) and in time (arm=300/0) both failed because
  losing trades keep losing. The inverse: cut them faster. Supporting data: BE arms at median
  0-11 ticks (p90 = 5-55), so only 2.5-15.1% of all trades arm after tick 30 -- a 30-tick wall
  keeps nearly every trade that ever arms while killing non-starters 3x sooner. The time exit
  (-$2.25) is also cheaper than the distance exit (-$5.43 to -$6.89).
- Variants: (a) --be-arming-ticks 30, (b) --pre-be-loss-threshold 2.0.
- Screen: windows 1 (W1), 57601 (W3), 201601 (W8), 316801 (W12), STF, 10k ticks.
- Proceed to 12 windows ONLY if a variant beats baseline in >= 3 of 4 screen windows.
- Stage 2 PASS: >= 9 of 12 AND on total.
- Expected risk for (b): a $2 stop is touched far more often than $5, and the post-BE
  counterfactual showed ~95% of dips recover -- so (b) may cut winners early. Report the
  profit_drop vs failed_to_reach_be split and the trail-exit count, not just totals.

## Test J — combination: 30-tick wall + a 1.5 threshold (written before running)
- Two readings of "cap at 1.5", both tested:
  (a) --be-arming-ticks 30 --post-be-loss-cap 1.5   (post-BE cap widened 1.0 -> 1.5)
  (b) --be-arming-ticks 30 --pre-be-loss-threshold 1.5 (pre-BE soft SL tightened 5.0 -> 1.5)
- Rationale for (a): cut non-starters fast pre-BE, but give trades that PROVED themselves by
  reaching BE more room. Distinct from the failed wide-cap tests, which went to $30-$50 and
  died from uncapped tail losses; $1.5 stays bounded.
- Screen: windows 1, 57601, 201601, 316801. Compared ex-exhausted against the SAME baseline.
- PASS the screen: beats baseline in >= 3 of 4, AND beats arm=30 alone (otherwise the second
  knob adds nothing and we keep the simpler change).
- Stage 2: all 12 windows, >= 9 of 12 and positive on total.
- OVERFITTING BUDGET NOTE: pre-BE thresholds tried so far = $5 (baseline), $2, now $1.5;
  post-BE caps = $1 (baseline), $1.5. Every extra value tested makes a lucky winner more likely.
  Whatever survives must be forward-tested on demo before it is trusted, not shipped on
  backtest evidence alone.

## Test L (REVISED 2026-09-24, before running) -- n-tick confirmation on top of the shipped config
- The original Test L (above) predated the spread gate and named a bare baseline. That is now
  the wrong comparison: the gate and the 30-tick wall are both live, so the only decision-
  relevant question is whether n-tick adds anything ON TOP of them.
- Baseline: --max-entry-spread-pips 1.0 --be-arming-ticks 30 (= the shipped config).
- Variants: the same plus --n-tick-confirmation 2, and 3.
- 12 date-pinned canonical windows, STF, 10k ticks, ex-exhausted.
- PASS requires BOTH:
  (a) beats the shipped config in >= 9 of 12 windows, AND
  (b) pooled per-trade expectancy improves.
  Beating the bare baseline is NOT sufficient -- same rule that rejected the $1.5 combinations.
- Report alongside totals: trade count dropped (n-tick discards signals that never confirm),
  win rate, and mean entry price vs the signal candle's close.
- KNOWN RISK: the entry is 98.4% RSI, a MEAN-REVERSION signal, while n-tick is a MOMENTUM
  filter -- it buys only after the bounce has started, i.e. later and worse on exactly the
  trades RSI wanted early. Smoke test (half week) showed win rate 40.1% vs 25-28% and zero
  pre-BE stops, but was -$16 ex-exhausted. Motivation, not evidence.
- Two concurrent streams maximum (three exhaust the MT5 terminal's connection slots).

## Test M (2026-09-25, before running) -- STF session-filter INVERSION

- **Hypothesis source**: W3 smoke test, production code (STF, staircase on, arm=30, 1.0p entry
  spread gate), filter on vs off. The live 08-18 UTC block (derived for MTF) removes the BETTER
  STF trades: blocked hours -$0.097/trade (n=3,050) vs traded hours -$0.204/trade (n=3,550),
  t=2.7, blocked better on 14/20 days. The filter-on run equalled the open-hours subset of the
  filter-off run trade-for-trade, so one unfiltered run per window yields every arm.
  **W3 generated the hypothesis and is excluded from grading.**
- **Arms** (all read from ONE unfiltered production run per window, tagged by true UTC hour):
  - SHIPPED: trade 19:00-07:59 UTC (block 08-18) -- the live config.
  - INVERTED: trade 08:00-18:59 UTC only (block 19-07). Same 11-hour block, flipped. No other
    hour selection -- single hours are unstable (session-filter-analysis.md, Finding 4).
  - OFF: all hours. Reported for reference, not graded.
- **UTC tagging**: candle frame = UTC + 3 before 2026-03-29, UTC + 5 from 2026-03-29 (broker
  follows EU DST; verified from week-close bars shifting Sat 00:59 -> 01:59 at 29 Mar, and the
  spread-gated rollover landing at 21:00 UTC = 17:00 NY under +5). Only W7 straddles the change.
- **Screen windows**: W6, W5, W7, W2 (protocol below).
- **PASS (screen)** requires ALL of:
  (a) INVERTED per-trade expectancy beats SHIPPED in >= 3 of 4 windows,
  (b) pooled per-trade expectancy improves, AND
  (c) pooled TOTAL loss is no worse than SHIPPED (inversion trades a different, not larger,
      population -- it must not buy a better average with more absolute loss).
- On PASS: expand to W10/W8 without looking at them first. On FAIL: stop; the W3 split was noise.
- Report per window: trades, total, per-trade, win rate for all three arms.

## Test N (2026-09-25, before running) -- tight entry spread gate (1 point = 0.1 pip)

- **Hypothesis source** (exploratory, the 10 Test M windows): the pre-BE leak is ~90% of net
  loss, and it is almost entirely the 30-tick `failed_to_reach_be` timeout. Entry spread is the
  dominant predictor of that timeout (AUC 0.75; 0.0 pip -> 0% timeouts, 0.2 -> 29%, >=0.5 -> 52%+);
  RSI/MACD values carry no information (AUC 0.50). Largely mechanical: a zero-spread entry is born
  at breakeven. Windows differ mostly by broker spread regime (zero-spread entries: W2 63%, W1 43%
  vs W5/W6 0%), not market regime. Pooled, entries <= 0.1 pip averaged -$0.13/trade vs -$0.51 for
  all. Those 10 windows are spent and are NOT graded.
- **Arms** (both = live config: STF, staircase, arm=30, DST-correct session filter ON blocking
  08-18 UTC):
  - SHIPPED: entry spread gate 1.0 pip (`MAX_SPREAD_POINTS = 10`).
  - TIGHT: entry spread <= 1 point = 0.1 pip. Live form: `MAX_SPREAD_POINTS = 1.5` (spreads are
    whole points; 1.5 avoids `_spread_ok`'s float `<=` rejecting a 1-point spread computed as
    1.0000000001).
  Both read from ONE run per window (`--max-entry-spread-pips 1.0`, session filter OFF), using the
  logged `entry_spread_pips` (TIGHT = round(entry_spread_pips * 10) <= 1) and the production
  `SessionFilteredSignalStrategy._utc_hour` (SHIPPED session = UTC hour not in 08-18). Trades are
  simulated independently, so a subset equals a gated run.
- **Secondary, not graded**: the same TIGHT-vs-SHIPPED comparison under all hours and under the
  inverted session (Test M).
- **Windows** (unseen; tick data verified): W13 2025-09-23, W14 08-26, W15 07-29, W16 07-01,
  W17 06-03, W18 05-06, W19 04-08, W20 03-11, W21 02-11, W22 01-14, W23 2024-12-17, W24 11-19
  (each start date + 28 days). **Stage order fixed now, spread across the year, not chosen by
  spread regime**:
  - Stage 1 (direction): W13, W16, W19, W22
  - Stage 2 (confirm): W14, W20
  - Stage 3: W15, W17, W21, W23
  - Stage 4: W18, W24
- **Per-window win**: TIGHT $/trade > SHIPPED $/trade. If TIGHT has < 100 trades in a window
  (it abstained through a wide-spread regime) per-trade is not graded; TIGHT wins that window iff
  its total loss is smaller.
- **Stage bars (cumulative)**, each ALSO requiring pooled per-trade (TIGHT's own trades) better and
  pooled total no worse:
  - Stage 1: >= 3 of 4 -- sets direction. Fail -> stop.
  - Stage 2: >= 5 of 6. Fail -> stop.
  - Stage 3: >= 8 of 10.
  - Stage 4: >= 9 of 12 -> PASS = candidate to ship (`MAX_SPREAD_POINTS = 1.5`, user decision).
- **Report per window**: trades and % kept, zero-spread share, $/trade and total for both arms.
- **Known risk**: demo-account spreads. On a real account with typical spreads >= 0.2 pip, TIGHT
  would barely trade -- which would itself say the strategy only survives near-zero spread.
- Reserve windows W25-W30 (2024-06-04 .. 2024-10-22) stay unseen.

## Test O (2026-09-25, before running) -- wait up to 15 s for zero spread vs the 0.1-pip gate

- **Hypothesis source** (exploratory; all spent windows): a zero-spread entry is born at breakeven,
  so it cannot hit the 30-tick pre-BE timeout (~90% of net loss). A timeout-proxy over 22 windows
  showed "wait up to 15 s for zero spread, else skip" keeps as many signals as the 0.1-pip gate in
  tight-spread months with 0% timeouts. W1 mechanism check through the production wrapper (all
  hours): 6,666 trades, +$63.20, 0 timeouts vs -$411.20 without the wait. W1 is spent and NOT graded.
- **Arms** -- production code only, live config (STF, staircase, arm=30, DST-correct session filter
  ON), two runs per window:
  - Run A `--spread-wait off --max-entry-spread-pips 1.0` -> **OLD** (1.0-pip gate, all entries) and
    **TIGHT** (subset with round(entry_spread_pips * 10) <= 1, i.e. the 0.1-pip gate shipped on this
    branch; trades are independent, so a subset equals a gated run).
  - Run B `--spread-wait on --max-entry-spread-pips 0.15` -> **WAIT** (15 s, <= 0.5 pt, live gate).
- **Graded comparison: WAIT vs TIGHT** (TIGHT ships in the same PR and is the simpler alternative).
  OLD is reported, not graded.
- **Per-window win**: WAIT $/trade > TIGHT $/trade. If either has < 100 trades, graded on total.
- **Windows** (unseen for any test; ticks verified back to at least 2023-01), each start + 28 days:
  W31 2024-05-07, W32 04-09, W33 03-12, W34 02-13, W35 01-16, W36 2023-12-19, W37 11-21,
  W38 10-24, W39 09-26, W40 08-29, W41 08-01, W42 07-04. **Stage order fixed now, spread across the
  year, not chosen by spread regime**: Stage 1 W31, W34, W37, W40; Stage 2 W32, W38; Stage 3 W33,
  W35, W39, W41; Stage 4 W36, W42.
- **Cumulative stage bars**, each ALSO requiring pooled $/trade better and pooled total no worse
  (WAIT vs TIGHT): Stage 1 >= 3/4 (direction; fail -> stop), Stage 2 >= 5/6 (fail -> stop),
  Stage 3 >= 8/10, Stage 4 >= 9/12 -> PASS.
- **On FAIL**: set `USE_SPREAD_WAIT_ENTRY = False` before the PR merges; the 0.1-pip gate stands on
  Test N.
- **Report per window**: spread regime (share of Run A entries at <= 1 pt), trades, timeout share,
  $/trade, total for OLD / TIGHT / WAIT; WAIT expiries and mean wait.
- **Known risks**: in wide-spread regimes both TIGHT and WAIT mostly abstain (graded on total, near
  ties). Demo-account spreads -- on a real account zero-spread ticks may be rare.

## Test P1 (2026-09-26, before running) -- lab screen: RSI fade on M5, +/-5 pip fixed exits

- **Hypothesis source** (exploratory, 14 spent windows; docs/plans/in-progress/entry-timing.md):
  at M1 scale every signal's edge (~+0.02-0.03 pip) is smaller than friction (~0.1 pip). Reversion
  signals' edge grows with horizon faster than friction; RSI fade on M5 with +/-5 pip barriers, first
  entry per episode: win 55.7%, edge +7.9 pp over same-tick random, net +0.51 pip/trade, positive in
  8/12 windows (below the 75% bar set for that scan). Spent windows are NOT graded.
- **Rule (exact)**: EURUSD M5 candles; RSI(14), Wilder smoothing on close (`signal_lab._rsi`). At a
  candle close, RSI < 30 -> buy, RSI > 70 -> sell; keep only the first candle of each consecutive
  same-direction run (one entry per episode). Entry only in live session hours (DST-correct filter,
  08-18 UTC blocked) at the first tick within 15 s of the candle close whose spread <= 0.5 point;
  otherwise no trade.
- **Exit (screen model)**: fixed +5 / -5 pip barrier from the entry tick; buy entered at ask and
  judged on bid, sell entered at bid and judged on ask. Net pips = win x (5 - 0.04) - loss x
  (5 + 0.09) (measured fill slippage). Reported alongside: same-tick random direction; +/-3 pips
  (secondary, not graded).
- **Windows** (unseen for any test; ticks verified): W25 2024-10-22, W26 09-24, W27 08-27, W28 07-30,
  W29 07-02, W30 06-04 (each start + 28 days). One stage.
- **PASS requires all of**: pooled net > 0 pip/trade; pooled edge over same-tick random > 0; and at
  least 2/3 of counted windows net-positive, where a window counts if it has >= 20 trades and at
  least 3 windows count. Fewer than 3 counted windows -> INCONCLUSIVE (spread regime too wide).
- **On PASS**: build it in production (M5 RSI-fade entry + fixed pip exits, which production lacks)
  and run a pre-registered full-lifecycle test on unseen W43-W48 (2023-01-17 .. 2023-07-04) and older
  2022 windows. **On FAIL**: stop this line; write up that M1-scale entry work is friction-bound.
- **Known risks**: ~$10 risk per trade at 0.2 lots vs ~$1 today; hours-long trades; the screen ignores
  overlapping positions (an episode can start while a previous trade is open).
- Command: `PYTHONPATH=. pipenv run python scripts/signal_lab.py --p1-screen`
- **RESULT (2026-09-26): INCONCLUSIVE.** W25-W29 (Jul-Nov 2024) produced 0-1 trades each: a
  wide-spread period in which the zero-spread entry never fires. Only W30 (Jun 2024) counted: 50
  trades, win 60.0%, net +0.94 pip/trade at +/-5 (+0.42 at +/-3). Pooled 51 trades, 1 counted window
  (bar needs >= 3). Not a pass; the windows cannot answer the question.

## Test P1b (2026-09-26, before running) -- Test P1 re-run on tight-spread unseen windows

- **Why**: Test P1 was INCONCLUSIVE -- its windows (W25-W30, Jul-Nov 2024) were a wide-spread period in
  which the zero-spread entry never fires. Spread regime of older data was checked WITHOUT looking at
  any outcome (zero-spread tick share on one mid-window day): W43-W48 (2023 H1) 80.7-84.9%; 2022
  mostly 66-90%.
- **Rule, exit, slippage, pass bar**: identical to Test P1 (RSI(14) fade on M5, first entry per
  episode, live session hours, 15 s zero-spread entry, fixed +/-5 pip graded / +/-3 secondary; PASS =
  pooled net > 0, pooled edge over same-tick random > 0, >= 2/3 of counted windows net-positive,
  window counts at >= 20 trades, >= 3 counted windows, else INCONCLUSIVE).
- **Windows** (unseen): W43 2023-06-06, W44 05-09, W45 04-11, W46 03-14, W47 02-14, W48 01-17.
- **On PASS**: production build, then a pre-registered staged full-lifecycle test on W49-W60
  (2022-01-18 .. 2022-12-20 window starts), which stay unseen until then; 2021 in reserve.
  **On FAIL**: stop this line.
- Command: `PYTHONPATH=. pipenv run python scripts/signal_lab.py --p1b-screen`

## Testing protocol from 2026-09-24: staged escalation

Full 12-window runs cost hours. From here, hypotheses escalate through window sets, and only
earn the next set by passing the current one.

| Stage | Windows | Purpose |
|---|---|---|
| Screen | **W6, W5, W7 + W2** | 3 worst + 1 calm |
| Expand 1 | + W10, W8 | 6 windows |
| Expand 2 | + W9, W12 | 8 windows |
| Full | + W4, W3, W1 | 12 windows |

Severity ranking under the shipped config (gate 1.0p + arm=30), worst first:
W6 -$1.125/trade, W5 -$0.950, W7 -$0.883, W10 -$0.824, W8 -$0.814, W9 -$0.712,
W12 -$0.690, W11 -$0.683, W4 -$0.669, W3 -$0.441, W1 -$0.356, W2 -$0.324.

**Why a calm window is in the screen set.** Screening only on the worst windows biases toward
changes that repair high-volatility damage while quietly hurting calm conditions. That already
happened once: `arm=30` gained most in volatile windows (+$672, +$518) and *lost* in W1, the
calmest (-$120). Including W2 forces a change to avoid wrecking calm conditions before it
earns more windows.

**Expansion windows must stay unseen.** Each hypothesis screened on the same 4 windows spends
their credibility -- screen ten ideas there and one passes by luck. Do not look at an expansion
window's number while iterating on the screen set; that converts out-of-sample data into
in-sample data and is exactly how the wide-cap result happened.

**Pass bars stay as before**: a variant must beat the SHIPPED config (not a bare baseline), on
both window count and pooled per-trade expectancy.
