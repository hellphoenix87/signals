# Realistic Lot Sizing (Decouple from Demo Account's Inflated Balance)

Status: todo
Mode: MVP/POC (main session plans and implements directly; no subagents, no tests, single PR at the end)

## Goal

`RiskManager.calculate_lot_size` is sized correctly (risk `LOT_RISK_PERCENT` of balance per stop-loss distance, clamped to the symbol's real `volume_min`/`volume_max`/`volume_step`) but is fed the *live* MT5 account balance (`app/trade_execution/trade_execution.py::_resolve_lot`), which on this demo account is $100,000 -- producing ~20 lots per trade at the validated `DEFAULT_SL_PIPS=5`/`LOT_RISK_PERCENT=1%`. That's proportionally correct but not representative of what a realistically-funded account would trade. User confirmed: assume a **$1,000** balance for sizing purposes instead (~0.2 lots per trade at the current stop/risk settings) -- this account's actual balance stays $100,000 (nothing about the demo account itself changes), only what `RiskManager` is told to size against.

## Out of scope (explicit, not forgotten)

- Changing `LOT_RISK_PERCENT` itself (1%) -- unrelated question, not raised here.
- Any change to `DEFAULT_SL_PIPS`/`DEFAULT_TP_PIPS` -- separate from this, tracked in the concurrent `retune-zero-spread` plan.
- No tests, per MVP/POC mode. Verified via direct calculation.

## Phase 1: Config override + wiring

- **`app/config/settings.py`**: add `RISK_SIZING_BALANCE_OVERRIDE: Optional[float] = 1000.0` -- when set, `_resolve_lot` uses this instead of the live account balance for the risk-percentage calculation. `None` would fall back to the real live balance (not used here, but keeps the override meaningfully optional if ever needed).
- **`app/trade_execution/trade_execution.py::_resolve_lot`**: read `getattr(Config, "RISK_SIZING_BALANCE_OVERRIDE", None)`; if set, use it in place of `account_info.balance` when calling `risk_manager.calculate_lot_size`. Live balance is still fetched (harmless) but only used as the fallback when the override is `None`.

## Verification (manual, per MVP/POC mode)

- Confirm `Config.RISK_SIZING_BALANCE_OVERRIDE == 1000.0` via direct import.
- Confirm `TradeExecutor._resolve_lot` calls `risk_manager.calculate_lot_size` with `1000.0` (not the live $100,000 balance) when the override is set, via direct construction with a mocked `market_data.get_account_info()`.
- Recompute by hand: $1,000 balance x 1% risk / (5 pips x 0.0001 x 100,000 contract size) = 0.2 lots -- confirm this is what `calculate_lot_size` actually returns for `DEFAULT_SL_PIPS=5`.
