# USDJPY test: the momentum leads and the live system on unseen data

Status: in progress (branch `usdjpy-test`, stacked on `momentum-exit-grid` / PR #82; written 2026-09-26)

## Goal

All EURUSD tick data from 2021-03 onward has been spent on entry hypotheses. USDJPY has never been used for
an entry test, so it is the first honest out-of-sample check available. This plan adapts the tools to
USDJPY and runs a small set of hypotheses **once**, with every hypothesis and bar fixed here before any
USDJPY outcome is looked at.

## Adjustments for USDJPY (decided from spread data only -- no outcomes seen)

- Pip = 0.01, point = 0.001. All exits stay in pips.
- **Entry gate.** A spread-only check (the first 5 trading days of each window below; no prices-after-entry
  looked at) found that USDJPY's spread regime changes a lot by period. The live gate (<= 0.5 point within
  15 s) fires on only 35% of London/NY M1 closes (median window) and on ~0% in 2023-10 .. 2026-04. A
  <= 5 point (0.5 pip) gate fires on 99.8% of them. **Gate for this test: first tick with spread <= 5
  points within 15 s.** Entries then pay a real spread of up to 0.5 pip, and the lab accounts for it (buys
  enter at the ask and are judged on the bid).
- The 2025-04 window sits in an 18-point (1.8 pip) spread regime and the gate never fires there. It stays
  in the list, contributes no trades, and is reported as such.
- Slippage: EURUSD's measured values are reused (stops 0.09 pip past the level, targets 0.04 pip short).
  They have not been measured for USDJPY -- an assumption.
- Hours: London/NY = 08-18 UTC (DST-correct, the production conversion).

## Windows (28 days each, fixed)

2021-04-06, 2021-10-05, 2022-04-05, 2022-10-04, 2023-04-04, 2023-10-03, 2024-04-02, 2024-10-01,
2025-04-01, 2025-10-07, 2026-04-07, 2026-08-04.

## Pre-registered hypotheses (lab, `scripts/signal_lab.py --symbol=USDJPY`)

| id | signal | timeframe | hours | exit |
|---|---|---|---|---|
| H1 | production MACD (the EURUSD lead) | M1 | London/NY | fixed +3/-3 pips |
| H2 | KAMA(10,2,30) 3-bar slope, only when ATR10/ATR100 >= 1.3 | M1 | London/NY | fixed +3/-3 pips |
| H3 | same as H2, on M5 bars | M5 | London/NY | fixed +5/-5 pips |

- Every candle the signal fires on is a trade (this is how the lab works; trades overlap). A random
  direction at the same ticks is reported alongside each hypothesis.
- **Bar, for each hypothesis separately:** pooled net pips/trade > 0 after slippage, AND edge over
  same-tick random > 0, AND net positive in >= 2/3 of the windows with >= 50 trades. Pass or fail is
  judged once; no parameter or exit is changed after seeing results.
- These are three tests, so one pass out of three by luck is plausible; a pass earns a production run,
  not a verdict.

## Production arm (live code, report only)

- H4: the live configuration as it is, on USDJPY:
  `scripts/backtest_exit_strategy.py --full-lifecycle --single-timeframe --symbol USDJPY
  --spread-wait-max-points 5` over the same 12 windows (live exits: $1 post-BE cap, $2 staircase, in
  money, so ~0.7 / ~1.4 pip at 0.2 lot on USDJPY). Session filter as configured (no blocked hours for
  USDJPY). Reported: trades, the three outcome buckets (never reached BE / BE but not staircase /
  staircase), $/trade, t, windows positive.

## Phases

### Phase 1: Adapt the lab
- `--symbol=` (pip, point, MT5 symbol, production MACD symbol), `--windows=jpy` (the list above),
  `--gate-points=` (spread-wait gate), `--hypotheses` mode that prints exactly H1-H3 against the bar.
- Acceptance: the EURUSD defaults are unchanged (the `--cached` live table reproduces as before).

### Phase 2: Run H1-H3 (lab) and H4 (production)

### Phase 3: Write-up, PR (the user merges)

## QA
