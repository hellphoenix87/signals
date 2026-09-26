# Momentum exit grid: can momentum entries be ridden with wider exits?

Status: done 2026-09-26 -- no smoke pass (branch `momentum-exit-grid`, stacked on `m1-momentum-entry` / PR #81; written 2026-09-26)

## Goal

m1-momentum-entry found no M1 momentum entry that beats random at the live +1/-0.5 pip exit. The best
candidates only showed up in London/NY hours with volatility expansion. The live exit is sized to noise
(a 0.5 pip cap), so a momentum trade gets closed before it can run. This plan screens the momentum
candidates against wider fixed exits and trailing exits, to see whether an edge appears (or grows) once
trades have room.

**This is a smoke screen on the signal lab (`scripts/signal_lab.py`), not a result.** Any candidate that
survives must be built as a production strategy and run through `--full-lifecycle` before it counts.

## Design (fixed before running)

- Hours: `ldn_ny` (08-18 UTC) is the primary set; `live` (19-07 UTC) is for contrast.
- Entries: the lab's live entry (the first zero-spread tick within 15 s of the M1 close).
- Candidates (from the m1-momentum-entry screen, all with ATR10/ATR100 >= 1.3): KAMA slope, Keltner
  breakout, ROC break, Hull slope. Also KAMA slope and Keltner breakout alone, and the baselines RANDOM
  and the production MACD.
- Exits (pips, both directions played from the same tick):
  - fixed target/stop: target {1, 2, 3, 5} x stop {0.5, 1, 2, 3} -- 16 pairs;
  - trailing: initial stop S; once the peak is >= A, the stop follows at peak - D (never below the initial
    stop): (S, A, D) = (1,1,1), (2,2,2), (3,3,3), (2,1,1), (3,2,2), (2,2,1), (5,3,3) -- 7 trails.
- Costs: stop exits (fixed stops and all trailing exits) fill 0.09 pip beyond their level, and targets
  0.04 pip short, both measured on live runs. Unresolved trades (> 200k ticks) are dropped and counted.
- Metric: net pips per trade and its edge over the same-tick random direction; $ at 0.2 lot = 2 x pips.
- **Tuning split:** exits are chosen on the TUNE windows (W1, W2, W31-W35) and reported on the HOLDOUT
  windows (W36-W42) without re-tuning. All windows are spent for entry hypotheses, so the holdout only
  guards against fitting the exit to noise. It is not a confirmation.
- Smoke pass: a candidate's tuned exit has net pips/trade > 0 on HOLDOUT, positive in >= 5/7 holdout
  windows, and beats RANDOM under the same exit on HOLDOUT. A pass earns a production run, nothing more.

## Phases

### Phase 1: Exit grid in the lab
- Change: an `--exit-grid` mode computes both-direction outcomes for every fixed and trailing exit above
  in one scan per entry, caches them per hour set, and prints tune/holdout tables per candidate.
- Acceptance: the fixed (1, 0.5) column reproduces the lab's existing +1/-0.5 win rates.

### Phase 2: Run and write up
- Run `ldn_ny` and `live`; report every candidate x exit on both halves, and the tuned pick per candidate.
- Write-up in `docs/test-results/`, labelled smoke screen. If something passes, the next step is a
  production strategy + `--full-lifecycle` A/B (a separate plan, user decision).

## QA

- Phase 1: the fixed +1/-0.5 column agrees 100% with the barrier outcome (196,866 entries).
- Phase 2: no candidate passes the smoke bar in either hour set. The momentum edge does not grow with
  exit width. The production MACD in ldn_ny does (+0.09..+0.14 pip edge at +/-3 pip exits, in both
  halves), but only to about break-even. Added on request: an M5 extrapolation from candles
  (`scripts/m5_candle_extrapolation.py`): momentum at M5 is negative in the live hours and below cost
  in ldn_ny; fades are positive in the live hours. Report:
  docs/test-results/momentum-exit-grid-and-m5.md.

