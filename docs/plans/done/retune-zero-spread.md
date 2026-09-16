# Re-tune Ratio + RSI Weight Under Confirmed Real-Account Spread (~0)

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

User confirmed the `MetaQuotes-Demo` account is the actual account this bot will trade on (no real money, but otherwise the real trading conditions) -- so its measured near-zero spread (median 0, mean 0.016 pips historically; 0.0 pips live-checked) is the real cost to optimize for, not a demo-only artifact to correct for with an assumed 1-pip cost.

Every tuning decision since [spread-and-second-window-validation.md](../../docs/test-results/spread-and-second-window-validation.md) introduced spread modeling -- the ratio sweet-spot search (target=3-5 plateau for single-tf, target=7 for MTF), the RSI weight sweep (2.5) -- was optimized *assuming a pessimistic 1-pip spread*. If the real cost is ~0, those may not be the actual optima. This plan re-runs both sweeps at `--spread-pips 0` to find the genuinely correct configuration for the real trading conditions, rather than assuming the spread=1-optimized config transfers unchanged.

**Honest caveat carried into this plan**: `MetaQuotes-Demo` is MetaQuotes' own generic testing server, not a specific broker's demo -- flagged to the user directly; proceeding on their explicit confirmation that conditions should match.

## Out of scope (explicit, not forgotten)

- Re-validating the session filter's blocked hours under spread=0 -- that finding is about which hours show worse *directional* signal quality (volatility/price-action patterns), not spread cost directly, so it's assumed to still hold; not re-swept here unless the ratio/weight re-tune result calls it into question.
- Re-running the ML entry model comparison at spread=0 -- separate question, not part of this plan (that model already lost decisively at spread=1; unlikely to flip, not worth the compute here).
- Any change to `USE_MULTI_TIMEFRAME_SIGNALS`/`USE_SESSION_FILTER` on/off state -- unaffected by this plan.
- No tests, per MVP/POC mode. Verified via direct backtest runs against real MT5 historical data.

## Phase 1: Ratio re-sweep at spread=0

- Single-timeframe: target=3/4/5/6/7/8/9/10 pips at stop=5, `--spread-pips 0`, window 1 (`--start-pos 1`).
- MTF: same target range, `--session-filter` on (independent of spread, already validated), `--spread-pips 0`, window 1.
- Identify each strategy's new best ratio (smallest/most negative gap to that ratio's own breakeven, or crossing it) and compare against the spread=1-era optima (single-tf 3:5-5:5 plateau, MTF 7:5) to see whether the optimum actually shifted.

## Phase 2: RSI weight re-sweep at spread=0

- At single-timeframe's new best ratio (from Phase 1), sweep `--rsi-weight` 1.0/1.5/2.0/2.5/3.0/4.0 at `--spread-pips 0`, window 1.
- Confirm whether 2.5 (the spread=1-era optimum) still holds, or a different weight is better without spread pressure.

## Phase 3: Out-of-sample validation

- Re-run the new best configuration(s) for both strategies on window 2 (`--start-pos 28801`) to confirm the result isn't specific to window 1.

## Phase 4: Update production config if warranted

- If a materially better ratio/weight is found (not just noise-level difference from the current 7:5/2.5), update `Config.DEFAULT_TP_PIPS`/`DEFAULT_SL_PIPS`/`ENTRY_RSI_WEIGHT` accordingly, and update `Config`'s implicit spread assumption where documented in comments (`app/config/settings.py`, `session_filtered_signal_strategy.py`'s docstring references, etc.) to note the confirmed real-account spread rather than the earlier 1-pip placeholder.
- If the current config already turns out near-optimal under spread=0 too, no config change -- just document that the earlier tuning happens to transfer, and why.

## Verification (manual, per MVP/POC mode) -- all confirmed

- All sweeps run via `scripts/backtest_signals.py` with `--spread-pips 0`.
- **Phase 1 result**: the optimum genuinely shifted. Single-timeframe's spread=1-era plateau (3:5-5:5) sharpened into a peak at 4:5 (+4.3 pts) that then declines and crosses back below breakeven past ~7:5-8:5. MTF's spread=1-era single point (7:5, -8.0 pts under spread=1) became a much wider profitable zone -- every ratio from 3:5 to 10:5 clears breakeven, peaking at 8:5 (+9.2 pts), not 7:5.
- **Phase 2 result**: `ENTRY_RSI_WEIGHT=2.5` remains at least tied-best (59.2%-59.9% across 1.5-4.0, all within noise) -- no change needed.
- **Phase 3 result**: both new optima reproduced out-of-sample on window 2 -- single-tf 59.7% (vs. window 1's 59.9%, remarkably stable); MTF 44.4% (vs. window 1's 47.7%, shrinks but stays clearly positive, +5.9 pts).
- **Phase 4: `Config.DEFAULT_TP_PIPS` updated from 7.0 to 8.0`** (MTF's new optimum -- MTF is the live strategy via `USE_MULTI_TIMEFRAME_SIGNALS=True`). `DEFAULT_SL_PIPS` unchanged at 5.0. `ENTRY_RSI_WEIGHT` unchanged at 2.5. Confirmed all three via direct `Config` import.
- Full write-up: `docs/test-results/zero-spread-retune.md`, with explicit before/after comparison against the spread=1-era numbers.
