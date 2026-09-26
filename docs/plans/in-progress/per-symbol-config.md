# Per-symbol config: each pair gets its own settings, starting with USDJPY

Status: in progress (branch `per-symbol-config`, off master; written 2026-09-26)

## Goal

Each traded pair gets its own complete settings and trade config. EURUSD stays exactly as it is today. New
pairs start as a copy of EURUSD (the template) and change only what they need. First new pair: USDJPY.

## Design

- `Config` (app/config/settings.py) stays the EURUSD config and the template. Nothing in it changes.
- `app/config/symbols.py`: one subclass per extra pair, e.g. `class USDJPYConfig(Config)`. It inherits
  every EURUSD setting and overrides only what differs, each override with a one-line reason.
  `config_for(symbol)` returns the pair's class; any symbol without its own class gets `Config`.
- Wiring (live code): `app/factory.py` builds each orchestrator's candle collector, strategy and ExitTrade
  from `config_for(symbol)`. The shared `TradeExecutor` and `RiskManager` look up `config_for(symbol)` per
  signal / per sizing call (spread gate, default SL/TP, risk %, min SL).
- `ExitTradeConfig.for_config(cfg, **overrides)`: an ExitTradeConfig that takes every exit field `cfg`
  overrides relative to `Config`. For EURUSD (`cfg is Config`) nothing differs, so its exits are exactly
  today's `ExitTradeConfig()`.
- The production backtest (`scripts/backtest_exit_strategy.py`) uses `config_for(--symbol)` for the
  strategy, exits and window settings, so a USDJPY backtest runs the USDJPY config through live code.
- `Config.SYMBOLS` stays `["EURUSD"]`. USDJPY does not trade live until it passes its test and the user
  adds it.

## USDJPY starting config (decided without looking at USDJPY outcomes)

- **Hours:** trade 08-18 UTC only, i.e. block 19-07 UTC. The per-pair session study
  (docs/test-results/session-filter-per-pair-analysis.md) found USDJPY's 08-18 block reproducibly *better*
  (both windows) -- the opposite of EURUSD -- and said that reusing EURUSD's block on USDJPY would be
  harmful.
- **Money exits unchanged ($1 cap, $2 staircase, $5 soft SL).** Live lot sizing (1% of the $1,000 sizing
  basis over a 5-pip stop) gives USDJPY ~0.3 lot at ~150 yen, where a pip is worth ~$2 -- the same as
  EURUSD at 0.2 lot. So the same dollars already mean the same pip distances. (An earlier draft scaled the
  money exits by 0.68; that was wrong -- it only compensated for the backtest's fixed 0.2 lot.)
- **Lot sizing fix (live code):** `RiskManager.calculate_lot_size` computed a stop's cost in the quote
  currency, so a yen-quoted pair got a lot ~150x too small (clamped to the 0.01 minimum). It now converts
  to the account currency when the base currency is the account's (USDJPY). EURUSD is unchanged.
- **Backtest `--lot risk`:** sizes each window like the live code (real RiskManager at the window's first
  close) instead of a fixed 0.2 lot. USDJPY runs use it.
- **Unchanged from the template:** the entry signal and all its parameters, the spread-wait entry (zero
  spread within 15 s), the spread gate in points (a JPY point is 0.1 pip, the same as a EURUSD point), all
  pip-denominated settings.

## Phases

### Phase 1: Per-symbol config mechanism
- Change: `app/config/symbols.py` with `USDJPYConfig` as a **pure copy** (no overrides yet) and
  `config_for`; `ExitTradeConfig.for_config`; wiring in factory, executor, risk manager and the backtest.
- Acceptance: a EURUSD full-lifecycle backtest of one window gives the same trades and P&L as before the
  change (same CSV). No tests in MVP mode.

### Phase 2: USDJPY starting config
- Change: the overrides listed above, in `USDJPYConfig`.
- Acceptance: a USDJPY backtest prints the USDJPY exit values and session block.

### Phase 3: USDJPY development run (live code)
- Development windows: 28-day windows from 2021-2023 that do not overlap the usdjpy-test windows
  (2021-01-12, 2021-07-06, 2022-01-11, 2022-07-05, 2023-01-10, 2023-07-04).
- `backtest_exit_strategy.py --full-lifecycle --single-timeframe --symbol USDJPY --lot risk --start-date <w> --weeks 4`.
- Report per window: trades, the outcome buckets (never reached BE / BE not staircase / staircase),
  $/trade, total. Changes to the USDJPY config are allowed here, one at a time, each recorded with its
  result.

### Phase 4: USDJPY confirmation (only after Phase 3 settles the config)
- Freeze the config and pre-register the windows (2024-2026, not overlapping the usdjpy-test windows) and
  the bar in docs/test-results/pre-registrations-2026-09-22.md. Run once.

### Phase 5: Write-up and PR (the user merges; adding USDJPY to `SYMBOLS` is the user's call)

## QA
