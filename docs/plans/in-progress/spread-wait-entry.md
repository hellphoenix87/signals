# Wait-for-zero-spread entry

Status: in-progress (branch `spread-wait-entry`; PR opened only after Test O; do NOT merge before testing is finished -- user decision 2026-09-25)

## Goal

The 30-tick pre-breakeven timeout is ~90% of net loss, and it is mostly the cost of crossing the
spread: a zero-spread entry is born at breakeven (`is_break_even` is `profit >= 0`) and cannot time
out. Measured on 22 windows (55k live-hours signals, exploratory): **waiting up to 15 s after the
signal for a zero-spread tick, else skipping**, keeps 22% of signals (80% in tight-spread months,
same as the 0.1-pip gate) with **0.0% timeouts** and a slightly *better* entry price (+0.04 pip),
versus 8.3% timeouts for the 0.1-pip gate and 34.5% for the shipped 1.0-pip gate. Build it as a
production signal wrapper, drive that same wrapper from the backtest, and test it on unseen windows.

Expectation, stated up front: this removes the timeout leak, not the loss. Zero-spread entries
averaged -$0.064/trade (exploratory) -- every trade goes straight to the post-BE cap/trail balance.
Success means "less negative than the 0.1-pip gate", not "profitable".

## Out of scope

- Any change to exits (arming ticks, pre-BE SL, post-BE cap, staircase).
- Session hours (Test M inversion stays unshipped; the test runs under the live session filter).
- (Changed 2026-09-25: the user decided this PR SHIPS both the 0.1-pip gate and the 15 s spread
  wait -- see Subphase 1.5. The PR is not merged until Test O is finished.)
- MTF: the wrapper is built for the STF chain (live since PR #73). MTF compatibility is not tested.
- Tests: MVP/POC mode -- no pytest added. Acceptance criteria below are manual/smoke checks.

## Phases

### Phase 1: Production wrapper

#### Subphase 1.1: Config tunables -- DONE

- Change: `app/config/settings.py` -- add, next to `MAX_SPREAD_POINTS`:
  - `USE_SPREAD_WAIT_ENTRY: bool = False`
  - `SPREAD_WAIT_MAX_POINTS: float = 0.5` (spreads are whole points; 0.5 = "zero spread only" and
    avoids a float `<=` rejecting a 0-point spread computed as 1e-12)
  - `SPREAD_WAIT_SECONDS: float = 15.0` (measured from the signal candle's close)
  with a comment pointing at this plan and the numbers in Goal.
- Acceptance: `Config.USE_SPREAD_WAIT_ENTRY is False`; app imports unchanged.

#### Subphase 1.2: `SpreadWaitEntryStrategy` wrapper -- DONE

- Change: new `app/signals/strategies/spread_wait_entry_strategy.py`, class
  `SpreadWaitEntryStrategy(BaseSignalStrategy)`, modelled on `NTickConfirmedSignalStrategy`:
  - `__init__(strategy, max_spread_points, max_wait_seconds, entry_tf_seconds, logger=None)`;
    `__getattr__` forwards to the wrapped strategy.
  - `generate_signal(candles, ...)`: call the wrapped strategy. If it returns `buy`/`sell`, store it
    as pending with `deadline = last candle time + entry_tf_seconds + max_wait_seconds` (candle-frame
    datetime, same basis as `MarketData` candle times) and return the signal with
    `final_signal="hold"`, `reason="waiting_for_spread"`. A new candle's `buy`/`sell` replaces any
    pending one; `hold` leaves an unexpired pending signal in place.
  - `on_new_tick(price, spread_points=None, tick_time=None)`: if pending and `tick_time` is past the
    deadline -> drop it (log `spread_wait_expired`). Else if `spread_points is not None and
    spread_points <= max_spread_points` -> move pending into `_confirmed_signal`
    (`reason="spread_wait_confirmed"`, add `spread_wait_seconds` = tick_time - candle close).
    `spread_points is None` never confirms (fail closed: no spread, no entry).
  - `get_confirmed_signal(tick_time=None)`: pop and return `_confirmed_signal`.
- Acceptance (manual, in a REPL with a stub base strategy): a `buy` returns `hold`; ticks with spread
  2, 1, 0 confirm on the 0; a tick past the deadline expires it; `None` spread never confirms.

#### Subphase 1.3: Wire into `strategy_factory` -- DONE

- Change: `app/signals/signal_generation.py::strategy_factory` -- after the n-tick block and before
  the session filter, wrap with `SpreadWaitEntryStrategy` when `USE_SPREAD_WAIT_ENTRY` is true, using
  the three Config values and `TF_SECONDS` of `TF_ENTRY` (move or duplicate the `{M1: 60, M5: 300,
  M15: 900}` map into `app/` -- `app/` must not import from `scripts/`). If n-tick confirmation is
  also enabled (`USE_N_TICK_CONFIRMATION and N_TICK_CONFIRMATION > 1`), raise `ValueError` -- both
  own the pending-signal lifecycle and are not designed to stack.
- Acceptance: with the flag off the chain is unchanged (`SessionFilteredSignalStrategy ->
  StrongSignalStrategy`); with it on, `SessionFilteredSignalStrategy -> SpreadWaitEntryStrategy ->
  StrongSignalStrategy`; both flags on raises.

#### Subphase 1.4: Orchestrator passes real spread and tick time -- DONE

- Change: `app/services/trade_services.py::SignalOrchestrator._on_tick` -- today it reads
  `getattr(tick, "spread", None)`, which MT5 ticks do not have, so `spread_points` is always `None`.
  Compute `spread_points = (tick.ask - tick.bid) / point` using the broker's point size (reuse
  whatever `TradeExecutor._spread_ok` uses: `broker.get_point_size(symbol)`; if the orchestrator has
  no broker reference, pass the point size in at construction from `app/factory.py`). Call
  `on_new_tick(price, spread_points, tick_time=candle_frame_time)` with a `TypeError` fallback to the
  old two-argument call (orchestrator's defensive-call convention). Compute `candle_frame_time`
  before this call (it is currently computed after it).
- Acceptance: with the flag on and a live MT5 demo terminal, logs show `waiting_for_spread` on a
  signal candle and `spread_wait_confirmed` / `spread_wait_expired` within 15 s. With the flag off,
  behaviour is unchanged (n-tick is disabled live, so `on_new_tick` has no other consumer today).

#### Subphase 1.5: Ship it in this PR (user decision)

- Change: `app/config/settings.py` -- `MAX_SPREAD_POINTS = 1.5` (from 10; Test N, 12/12) and
  `USE_SPREAD_WAIT_ENTRY = True` with `SPREAD_WAIT_SECONDS = 15.0`, `SPREAD_WAIT_MAX_POINTS = 0.5`.
- Acceptance: `strategy_factory(config=Config, symbol="EURUSD")` chain is
  `SessionFilteredSignalStrategy -> SpreadWaitEntryStrategy -> StrongSignalStrategy`. The PR stays
  unmerged until Test O's verdict; if Test O fails, flip `USE_SPREAD_WAIT_ENTRY` back to False before
  merge (the 0.1-pip gate stands on Test N alone).

Phase 1.1-1.4 smoke (REPL, stub base strategy): buy -> hold/`waiting_for_spread`; ticks at spread
2, 1, None do not confirm; spread 0 at +4 s confirms (`spread_wait_seconds=4.0`); a spread-0 tick at
+16 s expires it; hold passes through. Factory: flag off `Session -> Strong`; on
`Session -> SpreadWait -> Strong`; with n-tick also on -> ValueError. Orchestrator spread: 1 pt ->
1.0000000000065 (the float edge the 0.5/1.5 thresholds exist for), 0 pt -> 0.0, no bid/ask -> None.

### Phase 2: Backtest drives the production wrapper

#### Subphase 2.1: `--spread-wait` in the full-lifecycle backtest

- Change: `scripts/backtest_exit_strategy.py` -- add `--spread-wait on|off` (and optional overrides
  `--spread-wait-max-points`, `--spread-wait-seconds`). It overrides `Config.USE_SPREAD_WAIT_ENTRY`
  for the run (Config now defaults it ON, so baseline runs must pass `off`), so `strategy_factory`
  builds -- or omits -- the **real** wrapper. Omitted = Config's own value. For each signal the wrapper marks pending, fetch ticks from the candle close (as today),
  feed them one by one through `strategy.on_new_tick(bid, (ask - bid) / point, tick_time=
  datetime.fromtimestamp(tick.time))` until `get_confirmed_signal()` returns the signal or it
  expires; enter at that tick (ask for buy, bid for sell) and run the existing lifecycle from there.
  Expired -> record `outcome="spread_wait_expired"` and exclude it from P&L (like
  `skipped_wide_spread`). Log `spread_wait_seconds` and `entry_spread_pips` per trade. Do **not**
  add a script-side reimplementation of the wait (the n-tick path's `_confirm_entry_tick` is the
  anti-pattern to avoid).
- Acceptance: on W1 (2026-08-25..09-22, STF, session filter off), every entered trade has
  `entry_spread_pips == 0`, `failed_to_reach_be` count is 0, and entered-trade count is within a few
  % of the exploratory estimate (~80% of signals in a tight-spread month). Run printout names the
  effective spread-wait settings alongside the existing exit-config line.

### Phase 3: Pre-registered staged test (Test O)

#### Subphase 3.1: Pre-register before any window is run

- Change: append Test O to `docs/test-results/pre-registrations-2026-09-22.md`:
  - **Arms** (all under the live config: STF, staircase, arm=30, DST-correct session filter ON):
    TIGHT = 1-point gate (Test N; shipped in this same PR, so it is the baseline to beat);
    WAIT = `--spread-wait on` (15 s, zero spread). OLD = the 1.0-pip gate, reported, not graded.
    TIGHT and OLD are read from one `--spread-wait off --max-entry-spread-pips 1.0` run per window
    via `entry_spread_pips`; WAIT needs its own run.
  - **Bar**: WAIT must beat TIGHT. Per-window win = better $/trade; if WAIT or TIGHT has < 100
    trades, graded on total.
  - **Windows** (unseen; ticks verified back to at least 2023-01): W31 2024-05-07, W32 04-09,
    W33 03-12, W34 02-13, W35 01-16, W36 2023-12-19, W37 11-21, W38 10-24, W39 09-26, W40 08-29,
    W41 08-01, W42 07-04 (each start + 28 days). Stage order fixed at pre-registration, spread
    across the year: Stage 1 W31, W34, W37, W40; Stage 2 W32, W38; Stage 3 W33, W35, W39, W41;
    Stage 4 W36, W42.
  - **Cumulative stage bars** (each also needs pooled $/trade better and pooled total no worse, vs
    TIGHT): >= 3/4, >= 5/6, >= 8/10, >= 9/12. Fail at any stage -> stop.
  - Report per window: spread regime (share of signals at <= 1 pt), trades kept, timeout share,
    $/trade, total, mean wait seconds.
- Acceptance: committed and pushed before the first Stage 1 run starts.

#### Subphase 3.2: Run stages 1-4 sequentially

- Change: no code. Per stage: run, grade against the pre-registered bar, record in this plan, commit
  + push. Two concurrent MT5 streams maximum.
- Acceptance: each stage's verdict recorded here before the next stage starts.

#### Subphase 3.3: Write-up

- Change: `docs/test-results/spread-wait-entry.md` (setup, per-stage tables, verdict, caveats:
  demo-account spreads; wide-regime windows mostly abstain; post-BE cap/trail now carries the result).
- Acceptance: a reader can see which arm won, by how much, and in which spread regimes.

## Open questions

- RESOLVED 2026-09-25 (user): wait length 15 s; the 0.1-pip gate ships in this PR, so it is Test O's
  baseline; the PR is not merged until Test O is finished.
- **Real-account spreads.** On a real account zero-spread ticks may be rare, so WAIT would mostly
  expire. Out of scope here; a demo forward run would be the check.

## QA

