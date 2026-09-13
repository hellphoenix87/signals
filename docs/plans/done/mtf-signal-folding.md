# Multi-Timeframe Signal Folding (Weighted Confluence + ADX)

Status: done
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

`MultiTimeframeStrongSignalStrategy` already exists and already folds M15/M5/M1 into one signal, but it does so by running the *same* single indicator (whatever `strategy_factory` was given, MACD by default) against all three timeframes and requiring strict boolean agreement across all three plus a pullback check. That's three reads of the same computation at different candle resolutions, not three genuinely independent opinions, and a single "hold" at any layer vetoes the whole thing with no partial credit.

This plan gives each timeframe layer its own, genuinely different indicator, adds ADX as a trend-strength gate on the bias layer (so a moving-average "trend" isn't trusted during a chop), and replaces the strict-AND combination with a weighted confluence score plus one hard veto (bias directly opposing entry). Layout, per the earlier design discussion:

- **M15 (bias)**: SMA crossover (`app/signals/indicators/sma_crossover.py`, already implemented, currently unused), re-tuned to longer windows appropriate for a "what's the higher-timeframe trend" read rather than its current fast-intraday defaults. Gated by ADX: if ADX is below a strength threshold, the bias layer's vote counts as neutral, since a moving-average crossover in a genuine chop is noise, not trend.
- **M5 (confirm)**: RSI (`app/signals/indicators/rsi.py`, already implemented, currently unused) — a genuinely different read (momentum/overbought-oversold) from either neighbor.
- **M1 (entry)**: MACD histogram acceleration (unchanged — this is what `strategy_factory` already builds as `base` when multi-timeframe is off, so the entry layer stays exactly as it is today).

Combination: each layer contributes `+weight`/`-weight`/`0` to a score (buy/sell/hold), summed and compared against a threshold to decide direction; a **hard veto** fires regardless of score if bias and entry are directly opposed (don't fight the immediate M1 read just because the higher-timeframe trend disagrees); the existing pullback-above-SMA20 gate still applies on top, unchanged, since it answers a different question (entry timing within the chosen direction, not which direction).

## Out of scope / explicit decision needed before this is live

- **This plan does not flip `Config.USE_MULTI_TIMEFRAME_SIGNALS` to `True`.** It builds and wires the whole mechanism correctly, with everything config-driven, but leaves the switch where it is (`False`) so turning on a new trading behavior is a deliberate, separate decision, not something that lands silently as part of implementing the mechanism. As discussed: none of this has been backtested, and the SL/TP defaults (`Config.DEFAULT_SL_PIPS = 2.0`) are a separate, likely more urgent problem than the signal-folding logic itself.
- **No backtesting infrastructure.** This plan doesn't build one; there's no way to validate the weights/threshold/ADX strength cutoff chosen here against real historical data. The defaults below are reasonable starting points, not tuned values.
- **No tests, per MVP/POC mode.** Verified via direct manual smoke checks (mocked MT5 boundary, real classes), same approach as the previous plan on this branch's lineage.

## Phase 1: ADX indicator

- New file `app/signals/indicators/adx.py::calculate_adx(candles, *, period=14) -> float`. Unlike every other indicator function in this codebase, this does **not** return `"buy"`/`"sell"`/`"hold"` — it's a strength gate, not a directional vote, so it returns a plain float (the ADX value, 0-100). Standard Wilder's-smoothed calculation from OHLC (`high`/`low`/`close`, all present on every candle dict — see `MarketData._rates_to_dict_list`): true range, +DM/-DM, Wilder-smoothed +DI/-DI, DX, then Wilder-smoothed DX. Returns `0.0` (treated as "no measurable trend strength," failing the gate safely) if there isn't enough data (needs roughly `2 * period` bars for a stable value).

## Phase 2: Restructure MultiTimeframeStrongSignalStrategy

- **`app/signals/strategies/strong_signal_strategy.py`**: remove the `apply_entry_filters` parameter from `generate_signal` — it's accepted today but the method's own comment admits nothing is implemented behind it ("Optionally, add entry filters here if needed"). Dead parameter, no behavior to preserve.
- **`app/signals/strategies/multi_timeframe.py`**: replace the single shared `base` strategy with three distinct collaborators — `bias_strategy`, `confirm_strategy`, `entry_strategy` (each a `StrongSignalStrategy`-shaped object with its own `generate_signal(candles)`), injected via the constructor rather than one instance reused three times. `generate_signal`:
  1. Runs each layer's strategy against its own timeframe's candles (unchanged `_get`/error-handling structure).
  2. Computes `adx = calculate_adx(c_bias, period=Config.MTF_ADX_PERIOD)` and treats the bias layer's vote as `"hold"` if `adx < Config.MTF_ADX_MIN_STRENGTH`.
  3. **Hard veto**: if the (possibly ADX-neutralized) bias vote and the entry vote are both directional and disagree, force `"hold"` — skip the weighted score entirely.
  4. Otherwise, weighted score: `bias_weight` if bias vote is buy, `-bias_weight` if sell, `0` if hold; same pattern for confirm/entry with their own weights; sum, compare against `Config.MTF_SCORE_THRESHOLD` (`>=` → buy, `<=` negative threshold → sell, else hold).
  5. Pullback gate (unchanged logic) applies on top of that direction, exactly as it does today.
  - Symbol resolution (`self.base.config.SYMBOLS[0]` fallback) becomes `getattr(self.config, "SYMBOLS", ["EURUSD"])[0]`, since the wrapper now holds its own `config` reference directly rather than borrowing one nested inside `base`.

## Phase 3: Wire it into strategy_factory and Config

- **`app/config/settings.py`**: add `MTF_BIAS_SMA_SHORT = 10`, `MTF_BIAS_SMA_LONG = 50` (M15-appropriate windows, distinct from `sma_crossover.py`'s own fast-intraday defaults of 5/20), `MTF_ADX_PERIOD = 14`, `MTF_ADX_MIN_STRENGTH = 20.0`, `MTF_BIAS_WEIGHT = 0.5`, `MTF_CONFIRM_WEIGHT = 0.3`, `MTF_ENTRY_WEIGHT = 0.2`, `MTF_SCORE_THRESHOLD = 0.6` (chosen so bias alone, at `0.5`, is *not* enough on its own — it needs at least one of confirm/entry to agree too; see the goal section's rationale).
- **`app/signals/signal_generation.py::strategy_factory`**: when `use_multi` is true, build `bias_strategy` (indicators={"sma": a `functools.partial(generate_sma_signal, short_window=Config.MTF_BIAS_SMA_SHORT, long_window=Config.MTF_BIAS_SMA_LONG)`}), `confirm_strategy` (indicators={"rsi": calculate_rsi}), and pass `entry_strategy=base` (the MACD strategy already built, unchanged) into `MultiTimeframeStrongSignalStrategy`, instead of building one `base` and passing it three times.

## Verification (manual, per MVP/POC mode)

- Direct smoke test (mocked MT5 boundary): construct `MultiTimeframeStrongSignalStrategy` with the three real per-layer strategies, feed synthetic M15/M5/M1 candle sets engineered to produce a known ADX value and known SMA/RSI/MACD reads, and confirm the combined decision matches hand-calculated expectations for: (a) all three agree -> fires, (b) bias below ADX threshold -> bias neutralized, score falls short -> holds, (c) bias and entry directly opposed -> vetoed regardless of confirm, (d) bias+entry agree but confirm neutral -> still fires (0.5+0.2=0.7 >= 0.6).
- Confirm `strategy_factory(config=Config)` with `use_multi=True` still builds without error and produces the same MACD-only single-timeframe strategy when `use_multi=False` (today's default), i.e. this plan doesn't change non-MTF behavior at all.
