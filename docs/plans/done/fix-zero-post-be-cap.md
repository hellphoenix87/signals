# Fix: ExitTradeConfig silently replaces a deliberate 0

Status: done

## Goal

`ExitTradeConfig` read three Config values as `getattr(Config, NAME, default) or default`, so a
deliberate 0 became the default: `EXIT_POST_BE_LOSS_CAP_MONEY = 0.0` (exit at breakeven) became $5,
`EXIT_BE_ARMING_TICKS = 0` (timeout disabled, per LossExitManager) became 20, and
`EXIT_TRAIL_GAP_FLOOR_MONEY = 0.0` became $2. Found 2026-09-26 while backtesting cap = breakeven:
the Config route silently ran a $5 cap.

## Out of scope

Other `or default` fields in `ExitTradeConfig`, where 0 is not a meaningful setting.

## Phases

### Phase 1: `_config_or_default` helper -- DONE

- Change: `app/exit_strategies/exit_trade.py` -- `_config_or_default(name, default)` falls back only
  when the setting is missing/None; used for the three fields above.
- Acceptance (manual): live Config -> 1.0 / 30 / 2.0 (unchanged); all three set to 0 -> 0.0 / 0 /
  0.0; all three deleted -> 5.0 / 20 / 2.0. Verified.
