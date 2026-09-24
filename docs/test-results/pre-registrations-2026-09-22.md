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
