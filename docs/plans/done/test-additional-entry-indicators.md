# Test Additional Entry Indicators (Bollinger Bands, Stochastic Oscillator)

Status: todo
Triage: low — purely additive backtest tooling (new indicator files, `build_indicator()` entries, CLI choices); no production Config defaults, live trading behavior, exit-strategy logic, or composition root (`app/factory.py`) touched.

## Goal

Only three indicators (MACD, SMA crossover, RSI) have ever been tried as the MTF M1 entry-trigger layer, in the investigation on PR #62 (`mtf-entry-indicator-ablation` branch). That investigation found:

- Entry-signal quality with MACD/SMA looks fine on **single-window** testing (`entry-excursion-trace.md`: 90-94% of trades reach a real favorable peak, averaging $4.73-6.12) — but this was **never cross-validated on a second window**.
- By contrast, the exit-strategy loss-cap sweep in the same investigation *was* tested on two windows, and every promising single-window result failed to replicate (`cap-5-second-window-test.md`: SMA swung from +$49.60 to -$299.00 between windows).
- RSI is structurally incompatible with the current pullback gate (0 signals in both windows tested) and is excluded from this plan.

This plan builds two new candidate M1 entry indicators (Bollinger Bands, Stochastic Oscillator) and puts them through the same rigor the exit-strategy side got — signal-quality proxy **and** direct entry-excursion tracing, on **two independent windows**, before any full-lifecycle P&L test is attempted. The point is to find out whether MACD/SMA's apparent entry-quality edge (a) is real and shared by other indicators, (b) is real but specific to those two, or (c) doesn't survive a second window either, the same way the exit-cap findings didn't.

## Out of scope

- Any change to `Config` defaults or live trading behavior — this is backtest-only tooling and analysis.
- The M15 bias (SMA) and M5 confirm (RSI) layers — unchanged throughout; only the M1 entry indicator varies, consistent with every test in the PR #62 investigation.
- Exit-strategy tuning (trail gap, loss cap) — reuse what PR #62 already established (tight trail: `--trail-gap-pct 0.10 --trail-gap-floor-money 0.0`; loss cap: `Config`'s own default, since the cap sweep found no value that beat it robustly) rather than re-litigating it here.
- Indicators beyond the two named below (Parabolic SAR, Ichimoku, Donchian/Keltner channels, etc.) — a natural follow-up plan, not this one.
- Any other symbol besides EURUSD.
- Unit tests — MVP/POC mode, per this repo's standing default; verification is by running the backtest scripts and reading their output, not pytest.

## Phases

### Phase 1: Build and wire Bollinger Bands as a candidate M1 entry indicator

#### Subphase 1.1: `app/signals/indicators/bollinger.py`

- Change: new file, `generate_bollinger_signal(data, *, window: int = 20, std_mult: float = 2.0, logger=None) -> str`, matching the existing indicator convention (`app/signals/indicators/sma_crossover.py::generate_sma_signal`'s shape — plain function, `data` a list of candle dicts, returns `"buy"`/`"sell"`/`"hold"`, catches exceptions internally and returns `"hold"` rather than raising). Logic: compute the simple moving average and standard deviation of the last `window` closes; `upper = sma + std_mult * std`, `lower = sma - std_mult * std`; return `"buy"` if the latest close `<= lower` (oversold, expect mean reversion up), `"sell"` if the latest close `>= upper` (overbought, expect mean reversion down), else `"hold"`. Returns `"hold"` if fewer than `window + 1` candles are available.
- Acceptance criteria: `python -c "from app.signals.indicators.bollinger import generate_bollinger_signal; ..."` run against a hand-built list of 25 candle dicts whose closes are flat except the last one, which is set far enough below the rolling mean to breach `lower`, returns `"buy"`; the mirror case (last close far above `upper`) returns `"sell"`; a list of fewer than `window + 1` candles returns `"hold"`.

#### Subphase 1.2: Wire Bollinger into `build_indicator()` and both backtest CLIs

- Change: `app/signals/signal_generation.py::build_indicator()` — add `if name == "bollinger": return functools.partial(generate_bollinger_signal, window=int(getattr(config, "ENTRY_BOLLINGER_WINDOW", 20)), std_mult=float(getattr(config, "ENTRY_BOLLINGER_STD_MULT", 2.0)))`, importing `generate_bollinger_signal` at the top of the file alongside the other indicator imports. Add `"bollinger"` to the `choices` list of the `--mtf-entry-indicator` argument in both `scripts/backtest_signals.py` and `scripts/backtest_exit_strategy.py` (currently `["macd", "sma", "rsi"]` in both).
- Acceptance criteria: `PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 1 --mtf --mtf-entry-indicator bollinger` runs to completion without error and prints a `=== Backtest summary ===` block (confirms the wiring end-to-end without waiting for a full 4-week run).

### Phase 2: Build and wire Stochastic Oscillator as a candidate M1 entry indicator

#### Subphase 2.1: `app/signals/indicators/stochastic.py`

- Change: new file, `generate_stochastic_signal(data, *, period: int = 14, oversold: float = 20.0, overbought: float = 80.0, logger=None) -> str`, same shape/error-handling convention as Subphase 1.1. Logic (mirrors `calculate_rsi`'s simplicity — a single latest-value threshold check, no crossover confirmation): `%K = 100 * (latest_close - lowest_low_over_period) / (highest_high_over_period - lowest_low_over_period)`, using each candle's `high`/`low`/`close`. Return `"buy"` if `%K < oversold`, `"sell"` if `%K > overbought`, else `"hold"`. Return `"hold"` if fewer than `period` candles are available, or if `highest_high == lowest_low` (flat range, undefined `%K`).
- Acceptance criteria: a hand-built 20-candle list where the last candle's close sits at the bottom of the period's high/low range (e.g. `close == lowest_low`) returns `"buy"`; the mirror case (`close == highest_high`) returns `"sell"`; a list of fewer than `period` candles returns `"hold"`.

#### Subphase 2.2: Wire Stochastic into `build_indicator()` and both backtest CLIs

- Change: same pattern as Subphase 1.2 — `build_indicator()` gets `if name == "stochastic": return functools.partial(generate_stochastic_signal, period=int(getattr(config, "ENTRY_STOCHASTIC_PERIOD", 14)), oversold=float(getattr(config, "ENTRY_STOCHASTIC_OVERSOLD", 20.0)), overbought=float(getattr(config, "ENTRY_STOCHASTIC_OVERBOUGHT", 80.0)))`; add `"stochastic"` to both CLIs' `--mtf-entry-indicator` choices.
- Acceptance criteria: `PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 1 --mtf --mtf-entry-indicator stochastic` runs to completion without error and prints a summary block.

### Phase 3: Two-window entry-quality validation — Bollinger Bands

#### Subphase 3.1: Signal-quality proxy, both windows

- Change: run `PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --mtf-entry-indicator bollinger` (window 1, default `--start-pos 1`) and again with `--start-pos 28801` (window 2). Write both runs' results (signal count, buy/sell split, win rate) into a new `docs/test-results/bollinger-entry-ablation.md`, following the format of `docs/test-results/mtf-entry-indicator-ablation.md`.
- Acceptance criteria: both windows produce **at least 10 buy/sell signals each** (rules out the RSI-style zero-signal structural failure before investing further effort); the doc states both windows' win rates side by side with an explicit verdict line: "consistent" (both clear or both miss the ~38.5% breakeven for target=8/stop=5 by a similar margin) or "inconsistent" (opposite sides of breakeven, or a swing of more than ~15 points between windows).

#### Subphase 3.2: Entry-excursion trace, both windows

- Change: run `PYTHONPATH=. pipenv run python scripts/backtest_exit_strategy.py --symbol EURUSD --weeks 4 --measure-entry-excursion --mtf-entry-indicator bollinger` on both `--start-pos 1` and `--start-pos 28801`. Append both windows' results (`% ever reached a favorable peak`, avg peak profit, avg % of peak retained) to `docs/test-results/bollinger-entry-ablation.md`.
- Acceptance criteria: doc states, for each window, the "ever reached a favorable peak" percentage and avg peak profit, plus an explicit verdict line: "consistent" (both windows show >70% ever-favorable, and avg peak profit within roughly 2x of each other) or "inconsistent" otherwise. **This verdict gates Phase 5** for Bollinger specifically — if inconsistent, Bollinger is documented as ruled out and Phase 5 is skipped for it.

### Phase 4: Two-window entry-quality validation — Stochastic Oscillator

#### Subphase 4.1: Signal-quality proxy, both windows

- Change: same as Subphase 3.1, substituting `--mtf-entry-indicator stochastic`, written to a new `docs/test-results/stochastic-entry-ablation.md`.
- Acceptance criteria: same as Subphase 3.1, applied to Stochastic's own doc.

#### Subphase 4.2: Entry-excursion trace, both windows

- Change: same as Subphase 3.2, substituting `--mtf-entry-indicator stochastic`, appended to `docs/test-results/stochastic-entry-ablation.md`.
- Acceptance criteria: same as Subphase 3.2, applied to Stochastic's own doc. **This verdict gates Phase 5** for Stochastic specifically.

### Phase 5: Conditional full-lifecycle P&L test (only for indicators that passed Phase 3/4)

#### Subphase 5.1: Full-lifecycle test for each two-window-consistent candidate

- Change: for each of Bollinger/Stochastic whose Phase 3/4 (or 4/4) verdict was "consistent," run `PYTHONPATH=. pipenv run python scripts/backtest_exit_strategy.py --symbol EURUSD --weeks 4 --full-lifecycle --trail-gap-pct 0.10 --trail-gap-floor-money 0.0 --mtf-entry-indicator <name>` (the tight-trail config already established as better than the original 60%/$2 trail in PR #62) on both windows. Write results into a new `docs/test-results/<name>-full-lifecycle-test.md`, comparing total P&L and win rate against the already-established SMA/MACD baselines at the same tight-trail, default-cap config (SMA: -$37.00/28.8% window 1; MACD: -$19.20/31.5% window 1 — window 2 baselines do not yet exist for this exact config and should be run fresh alongside the new indicator for a fair comparison, not assumed).
- Acceptance criteria: doc reports total P&L and win rate for the new indicator on both windows, side by side with freshly-run SMA/MACD numbers on the same two windows and same exit config, with an explicit closing verdict: does the new indicator beat both existing indicators on **both** windows, on **either** window, or on neither. No recommendation to change live `Config` is made regardless of outcome — that decision is explicitly left to the user, consistent with every finding in the PR #62 investigation.

## Open questions

None — indicator choice (Bollinger Bands, Stochastic Oscillator), scope (two indicators, not three), and the two-window gating rule were decided during planning based on the PR #62 investigation's findings.

## Outcome

Both candidates were built, wired, and put through the Phase 3/4 two-window signal-quality proxy — and both were **ruled out at that stage**:

- **Bollinger Bands**: 0 MTF-gated signals in both windows ([bollinger-entry-ablation.md](../../test-results/bollinger-entry-ablation.md)).
- **Stochastic Oscillator**: 2 and 8 MTF-gated signals across the two windows ([stochastic-entry-ablation.md](../../test-results/stochastic-entry-ablation.md)) — technically nonzero, but far below the plan's 10-signals-per-window bar and too small a sample to trust.

Both are same-bar contrarian/mean-reversion triggers, the same structural shape already shown to be incompatible with the MTF pullback gate for RSI (`mtf-pullback-gate-direction-fix.md`). Subphase 3.2/4.2 (entry-excursion trace) and Phase 5 (full-lifecycle P&L) were skipped per the plan's own gating rule, since there were no signals to trace or trade.

**Net result for MACD/SMA's apparent entry-quality edge**: outcome (b) from the Goal section — the edge looks specific to MACD/SMA (and structurally, to non-contrarian trigger shapes generally), not shared by other indicator families, at least not by mean-reversion ones under today's pullback gate. Trend/crossover-style indicators (Donchian, Parabolic SAR) remain untested and are a more promising direction for a follow-up plan than further contrarian/oscillator indicators.

## QA

(Not used — MVP/POC mode, no `qa` agent for this plan.)
