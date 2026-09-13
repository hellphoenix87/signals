# Activate Entry-Layer Indicators + Multi-Timeframe Backtest Validation

Status: todo
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

## Verification (manual, per MVP/POC mode)

- `strategy_factory(config=Config)` (use_multi defaulting from config, currently `False`) exposes `indicators.keys() == {"macd", "sma", "rsi"}`; `strategy_factory(config=Config, use_multi=True)`'s `entry_strategy` still exposes `indicators.keys() == {"macd"}` only.
- `scripts/backtest_signals.py --mtf` produces a nonzero signal count and a plausible-looking summary without placing any real order.
- Confirm the M5/M15 pointer alignment is genuinely causal (a candle isn't visible to the strategy before its own close time) by spot-checking a few M1 timestamps against the M5/M15 windows passed at that point.
