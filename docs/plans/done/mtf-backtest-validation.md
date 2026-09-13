# Activate Entry-Layer Indicators + Multi-Timeframe Backtest Validation

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

The live signal generator (`StrongSignalStrategy` via `strategy_factory`, single-timeframe path, active since `Config.USE_MULTI_TIMEFRAME_SIGNALS = False`) was firing buy/sell on over half of all M1 candles with a near-zero win rate at every stop size tested (`scripts/backtest_signals.py`, 4 weeks EURUSD: 0.4%-7.1% win rate across 5/10/20 pip stops). Root cause: only MACD was wired into the default `indicators` dict, so `confidence = total_votes / len(indicators)` was trivially 0 or 1 — nothing to disagree with, no real gating.

Two things, bundled since the second can't be judged without the first:

1. **Activate SMA + RSI alongside MACD in the single-timeframe path** (not the MTF path — see below), so `CONFIDENCE_THRESHOLD` reflects genuine multi-indicator agreement instead of a single rubber-stamped vote.
2. **The user's actual original design intent was multi-timeframe (SMA/M15 bias, RSI/M5 confirm, MACD/M1 entry)** — already built as `MultiTimeframeStrongSignalStrategy` but switched off (`USE_MULTI_TIMEFRAME_SIGNALS = False`) because it was never backtested. `scripts/backtest_signals.py` currently refuses to run when that flag is on (needs synchronized M1/M5/M15 history it doesn't fetch). Extend it to support that path, then run both the single-timeframe (3-indicator) and multi-timeframe strategies over the same historical window and compare.

## Out of scope (explicit, not forgotten)

- Turning `Config.USE_MULTI_TIMEFRAME_SIGNALS` on for live trading — that's a separate decision made *after* seeing backtest results, not part of this plan.
- Fixing the MACD indicator's own trigger condition (`hist_last > hist_prev` "accelerating histogram" rather than a true crossover) — flagged as a likely separate contributor to overtrading, but out of scope here; revisit if both strategies still look weak after this plan's changes.
- Full exit-strategy-aware backtesting (mark-to-market P&L via real exit managers) — still deferred per prior plans, unrelated to signal-generation quality.
- No tests, per MVP/POC mode. Verified via direct runs against real MT5 historical data.

## Phase 1: Activate SMA + RSI in the single-timeframe path

- `app/config/settings.py`: add `ENTRY_SMA_SHORT_WINDOW: int = 5`, `ENTRY_SMA_LONG_WINDOW: int = 20`, `ENTRY_RSI_PERIOD: int = 7` (defaults match the indicator functions' own defaults — no behavior surprise from adding the knobs).
- `app/signals/signal_generation.py::strategy_factory`: when `indicators is None` and `use_multi` is `False`, build `{"macd": ..., "sma": functools.partial(generate_sma_signal, ...), "rsi": functools.partial(calculate_rsi, ...)}` instead of MACD alone. When `use_multi` is `True`, keep the entry (M1) layer MACD-only, unchanged — the bias (M15/SMA) and confirm (M5/RSI) layers in `MultiTimeframeStrongSignalStrategy` already own those indicators; duplicating them at the entry layer too would double-count, not add signal.
- No changes needed in `trade_execution.py`/`trade_services.py` — both only ever read the resulting `final_signal` string, never `confidence`.

**Status: implemented, uncommitted on this branch as of plan creation.**

## Phase 2: Multi-timeframe backtest support in `scripts/backtest_signals.py`

- Add a `--mtf` flag (independent of `Config.USE_MULTI_TIMEFRAME_SIGNALS`, which stays `False` for live trading regardless of this flag) that runs the replay through `strategy_factory(config=Config, use_multi=True)` instead of the single-timeframe strategy.
- Fetch M1, M5, and M15 history up front (`MarketData.get_historical_candles`, same `Broker(TradingMode.BACKTEST)`/pip-size helpers as today), sized off the same `--weeks`/`--count` the user requests for M1, with the M5/M15 counts scaled down proportionally (M5 = M1/5, M15 = M1/15) since they cover the same calendar span with fewer bars.
- Alignment: `MultiTimeframeStrongSignalStrategy.generate_signal` expects `candles_by_tf: Dict[int, List[dict]]`, each a chronological list of *closed* candles as of "now." Replaying this correctly means, for each M1 index `i` (entry time `T`), passing every M5/M15 candle whose close time is `<= T` — not the full M5/M15 history. Maintain two monotonically-advancing pointers (via `bisect` over precomputed epoch-time arrays) into the M5 and M15 series as the M1 index advances, since all three series only grow forward together.
- Keep the existing `evaluate_signal` win/loss/undecided resolution unchanged — it already operates purely on the M1 series' own forward high/low, independent of which strategy produced the signal.
- Write MTF run results to a distinctly-named CSV (e.g. `{symbol}_mtf_{timestamp}.csv`) so single-timeframe and MTF runs don't overwrite each other, and print which mode ran in the summary header.

## Phase 3: Run and compare

- Run both `scripts/backtest_signals.py --weeks 4` (single-timeframe, 3-indicator) and `scripts/backtest_signals.py --weeks 4 --mtf` (multi-timeframe) against the same EURUSD window and stop-size sweep already used earlier (5/10/20 pips), plus a fixed baseline.
- Report signal count, win/loss/undecided, and win rate for both, side by side. No code changes expected in this phase unless the comparison surfaces an actual bug (e.g. MTF signal count of zero would indicate a wiring problem, not a real result, and should be root-caused before treating the win rate as meaningful).
- Decision on whether to flip `USE_MULTI_TIMEFRAME_SIGNALS` for live trading is the user's, made after seeing these numbers — not automated by this plan.

### Results (4 weeks EURUSD, target=50 pips throughout)

| Strategy | Stop (pips) | Signals | Win rate (decided) | Wins/Losses/Undecided |
|---|---|---|---|---|
| Single-tf (MACD only, pre-Phase-1) | 5 | 16,082 | 0.4% | 37/10326/5719 |
| Single-tf (MACD only, pre-Phase-1) | 10 | 16,082 | 1.1% | 62/5502/10518 |
| Single-tf (MACD only, pre-Phase-1) | 20 | 16,082 | 7.1% | 131/1706/14245 |
| Single-tf (MACD+SMA+RSI, Phase 1) | 10 | 5,657 | 1.3% | 28/2067/3562 |
| Multi-timeframe (SMA/M15, RSI/M5, MACD/M1) | 5 | 846 | 1.9% | 12/613/221 |
| Multi-timeframe | 10 | 846 | 5.3% | 18/322/506 |
| Multi-timeframe | 20 | 846 | 16.5% | 18/91/737 |

**Findings:**

1. Phase 1 (activating SMA+RSI in the single-timeframe path) cut signal count 65% (16,082 -> 5,657) but barely moved win rate (1.1% -> 1.3% at stop=10) — the vote-gating fixed overtrading, not the underlying lack of directional edge.
2. Multi-timeframe is far more selective (846 signals vs. 5,657-16,082) and beats both single-timeframe variants at every matched stop size — the weighted confluence + ADX gate is doing real filtering, not just noise reduction.
3. Even MTF's best result (stop=20) is a 16.5% win rate, well under the ~28.6% breakeven a 20:50 risk/reward requires, and 87% of those signals never resolved within 300 bars (undecided) — that figure rests on a small decided sample (n=109).
4. None of these configurations are net-profitable at `DEFAULT_TP_PIPS = 50`. Average bars-to-resolution creeps to 151 (~2.5 hours) at stop=20, suggesting 50 pips may simply be too far a target for M1 EURUSD to reach reliably — untested here, flagged as a natural next step.

**Decision (user's call, not automated by this plan): `Config.USE_MULTI_TIMEFRAME_SIGNALS` stays `False`.** MTF is directionally better than the single-timeframe path but not yet profitable at any stop size tested — not ready to trade live. Follow-up (separate future work, not this plan): re-run the MTF sweep with a target closer to what M1 EURUSD can realistically reach (e.g. 10-15 pips) before deciding whether to enable it.

## Verification (manual, per MVP/POC mode)

- `strategy_factory(config=Config)` (use_multi defaulting from config, currently `False`) exposes `indicators.keys() == {"macd", "sma", "rsi"}`; `strategy_factory(config=Config, use_multi=True)`'s `entry_strategy` still exposes `indicators.keys() == {"macd"}` only. Confirmed directly via `strategy_factory(config=Config)` -> `['macd', 'sma', 'rsi']`.
- `scripts/backtest_signals.py --mtf` produces a nonzero signal count and a plausible-looking summary without placing any real order. Confirmed (846 signals/4 weeks, all three stop sizes above).
- Confirmed the M5/M15 pointer alignment is genuinely causal by spot-checking 4 M1 timestamps against their M5/M15 windows: in every case the last visible higher-timeframe candle's close time was `<=` the M1 candle's own close time, and the next one (if any) was strictly later.

## Addendum: quick-check (1-minute exit) analysis

The fixed target/stop simulation above assumes a hold time (up to 300 bars) this app doesn't actually commit to — the real exit strategy is tick-driven and can close a position within seconds. Added `--quick-check` (with `--horizon-bars`, default 1) to `scripts/backtest_signals.py`: for each signal, look only at the next N M1 candles and report the best price seen in the signal's favor, the worst seen against it, and the net move at the close of the window — independent of any target/stop assumption. New functions `evaluate_immediate_move`, `write_quick_results_csv`, `summarize_quick`.

**Results (4 weeks EURUSD, horizon=1 bar/1 minute), plus a random-entry baseline (coin-flip direction on every M1 candle in the same window, not just signal candles) to judge whether the numbers mean anything:**

| Source | n | Had win opportunity | Never positive | Avg favorable | Avg adverse | Avg end |
|---|---|---|---|---|---|---|
| Random baseline (every candle) | 28,599 | 84.6% | 15.4% | +0.50 | +0.51 | -0.01 |
| Single-tf (MACD+SMA+RSI) | 5,656 | 85.3% | 14.7% | +0.60 | +0.63 | -0.04 |
| Multi-timeframe | 846 | 87.7% | 12.3% | +0.64 | +0.62 | -0.02 |

**Finding: statistically indistinguishable from random.** All three rows show ~85-88% "had a favorable moment" (this is just normal EURUSD noise over any 1-minute window, signal or not — a coin flip shows almost the same rate), favorable and adverse magnitudes nearly equal in every row (no skew toward the predicted direction), and average net move after exactly one minute ~0 pips in all three cases. Neither the single-timeframe nor the multi-timeframe strategy shows a detectable directional edge at a 1-minute horizon. This doesn't invalidate the earlier target/stop comparison (MTF still meaningfully outperforms single-timeframe there) — it means a fast, ~1-minute exit isn't where any edge would show up, if one exists; the edge (if real) needs more time to play out than one M1 bar.
