# Full-Lifecycle Exit-Strategy Backtest

**Question**: every prior exit-strategy backtest in this project stopped short of the real thing -- pre-BE-only, post-BE observed with no exit rule, or candidate rules tested independently of the real classes. Now that Thread 3 (trailing stop) and Thread 4 (post-BE loss cap) are both wired into the actual `ProfitExitManager`/`LossExitManager`, what does a trade's real, complete, single realized P&L look like under the system as it actually exists -- and does retuning the loss cap change now that it's measured against the real trail instead of in isolation?

## Method

New `--full-lifecycle` on `scripts/backtest_exit_strategy.py`. For each buy/sell signal, opens a simulated position and replays real historical ticks through the actual, complete `ExitTrade` -- `exit_trade._loss_manager.check_exit_on_tick` THEN (if no loss action) `exit_trade._profit_manager.check_exit_on_tick`, the same precedence `ExitTrade.on_tick` itself uses, gated the same way on `config.profit_exits_on_tick` (default `True`). One `PosState` carries continuously across the whole trade -- pre-BE arming through post-BE trailing/cap -- with no phase boundary, unlike every other function in this script. Reports the real exit reason (`profit_drop`, `failed_to_reach_be`, `profit_drop_after_be`, `trailing_breach_pct_of_peak`, `be_recovered_after_unprofit`, or `exhausted` if the 4,000-tick budget runs out first) and one real realized P&L per trade.

`--sweep-post-be-cap` extends this to retune Thread 4's cap against the real trail: replays several candidate cap values against the same tick stream in one pass, each with its own independent `LossExitManager` (different cap) and `PosState`, but the SAME real `ProfitExitManager` trail formula -- so only the cap value varies and each candidate's outcome reflects the real, complete combined system.

Two real bugs were found and fixed building this (not touching production behavior for live trading, which was unaffected by either):
1. `mt5.copy_ticks_from` returns numpy structured records (`t["bid"]` item access only); the real managers' `get_tick_value` only handles a plain dict or an attribute-accessible object (real live ticks are the latter). Silently returned `None` for `LossExitManager` all session (harmless -- the price/pips soft-SL variants it gates were never configured), but `ProfitExitManager` uses price unconditionally and crashed the first time this project ever fed it a real backtest tick. Fixed in the backtest script (`to_tick_dict`), not the managers.
2. `getattr(self.config, "post_be_loss_cap_money", 5.0) or 5.0` in `LossExitManager` silently discarded a deliberately-configured `0.0` (falsy) and fell back to `5.0` -- a real bug in the just-wired Thread 4 code. Fixed to only fall back when the attribute is genuinely absent.

Run across the same 7 majors x 3 non-overlapping 4-week windows as the rest of this project's exit-strategy work.

## Results

### 1. The first true "is the bot profitable" number

Pooled across all 7 pairs x 3 windows, 4,276 trades, at the *pre-retune* `-$3` cap:

| | Value |
|---|---|
| **Total realized P&L** | **-$839.91** |
| Win rate | 51.9% |
| Every pair | negative |

By real exit reason:

| Reason | Trades | % | Total |
|---|---|---|---|
| `trailing_breach_pct_of_peak` (Thread 3 trail) | 2,013 | 47.1% | **+$2,899.12** |
| `profit_drop_after_be` (Thread 4 cap) | 1,651 | 38.6% | **-$5,217.83** |
| `failed_to_reach_be` (pre-BE timeout) | 380 | 8.9% | -$511.78 |
| `exhausted` (still open at 4,000-tick cutoff) | 228 | 5.3% | +$2,011.28 |
| `profit_drop` (pre-BE soft SL) | 4 | 0.1% | -$20.69 |

**The cause is a win/loss size mismatch, not a low win rate.** Winning and losing trade *counts* are close to balanced (52.4% vs. 47.6%), but average sizes are not: trail exits average **+$1.44**, cap exits average **-$3.16** -- more than 2x. The $1.44 average traces directly to the trail's own math: `pct60_floor2` keeps ~40% of peak, and pooled median peak profit (measured earlier this session) is $3.40; 40% of $3.40 ≈ $1.36, matching the observed average almost exactly. Meanwhile the loss cap was a **flat** -$3, independent of how big any given trade's own peak ever got -- for a typical trade whose whole profit potential tops out around $3-4, a flat $3 loss cap is comparable to the entire win, not a small fraction of it.

### 2. Retuning the cap against the real trail (not in isolation)

`CAP_THRESHOLDS` (`docs/test-results/post-breakeven-loss-cap.md`) picked `-$3` over `-$5` in an isolated sweep run *before* Thread 3 was wired -- it never accounted for the real trail's actual win size. `--sweep-post-be-cap` retested against the real combined system:

**Pooled (3,931 trades, 7 pairs x 3 windows):**

| Threshold | Total P&L |
|---|---|
| **-$1.0** | **-$852.83** (best) |
| -$0.0 | -$903.19 |
| -$2.0 | -$931.72 |
| -$1.5 | -$981.24 |
| -$2.5 | -$1,007.59 |
| -$3.0 *(pre-retune)* | -$1,125.70 |

**EURUSD only (509 trades, 3 windows -- `Config.SYMBOLS` is EURUSD-only in production today, so this is the number that actually matters live):**

| Threshold | Total P&L |
|---|---|
| **-$1.0** | **-$23.80** (best) |
| -$2.0 | -$48.00 |
| -$1.5 | -$48.40 |
| -$2.5 | -$69.80 |
| -$3.0 *(pre-retune)* | -$77.40 |
| -$0.0 | -$95.40 (worst) |

`-$1.0` wins both pooled and EURUSD-only, beating the pre-retune `-$3` by $272.87 pooled / $53.60 on EURUSD alone. **Not a simple monotonic "tighter is always better" curve, though**: `$0` (cuts on literally the first tick of any negative noise -- 4.6% win rate pooled, 3.7% on EURUSD, 92%+ of trades hit) is worse than `$1.0`, the same failure mode the original `$0.04` trail bug had. There's a real valley around `$1.0`, not a floor-less slope.

## Decision

**Wired**: `Config.EXIT_POST_BE_LOSS_CAP_MONEY = 1.0` (was `3.0`), verified end-to-end.

**This is an explicitly temporary/interim value, not a final answer.** Even at the best-tested setting, the system is still net negative -- -$852.83 pooled, -$23.80 on EURUSD alone. Exit-side tuning (this cap and Thread 3's trail shape) has been swept about as far as bounded candidate sweeps can reasonably take it without producing a profitable system. The real lever left is entry-signal quality (Threads 1/2, both still open, plus the standing "passive drift" finding in `quick-check-multi-horizon.md`), not further retuning this number.

## Caveats

- `-$1.0` was not the tightest value tested (`$0` was), so this isn't a from-scratch optimum search -- but unlike the earlier `-$3` pick, this one did check the extreme (`$0`) and found it worse, so there's more confidence `$1.0` sits near a real local optimum rather than just being "as tight as we happened to try."
- `exhausted` trades (5.3% of the pooled sample, +$2,011.28) are mark-to-market profit at an arbitrary 4,000-tick cutoff, not a real closed outcome -- in reality those positions would still be running. Included as measured, not adjusted out.
- Same lot-size/entry-price/account-specific-spread caveats as the rest of this project's exit-strategy backtests apply unchanged.
- Candle-close profit exits (`profit_exits_on_candle_close`, default `False`) match the live default and are not exercised here.
