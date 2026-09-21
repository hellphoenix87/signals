# Full-Lifecycle Entry Indicator Comparison — MACD vs SMA

Date: 2026-09-20

## Setup

- **Motivation**: [mtf-pullback-gate-direction-fix.md](mtf-pullback-gate-direction-fix.md) recommended (and this session applied) switching the live MTF M1 entry indicator from MACD to SMA, based entirely on a signal-quality proxy (fixed target=8/stop=5 win-loss simulation against candle closes). That proxy has never been checked against the *real* exit strategy — tick-driven breakeven arming, post-breakeven trailing stop (`pct60_floor2`), and the loss cap (`EXIT_POST_BE_LOSS_CAP_MONEY=1.0`) — which is what the app actually runs live, not a fixed target/stop.
- **Found and fixed a bug in `scripts/backtest_exit_strategy.py` first**: `run()` never passed `symbol=symbol` to `strategy_factory`, so `SessionFilteredSignalStrategy` got an empty blocked-hours list regardless of `Config.USE_SESSION_FILTER=True` — session filtering was silently disabled in this script. Fixed (`strategy_factory(config=config, use_multi=True, symbol=symbol)`); trade count for SMA dropped from an uncorrected 1302 to a corrected 403, matching the entry-ablation's session-filtered signal count for the same window exactly.
- **Added `--mtf-entry-indicator {macd,sma,rsi}`** to this script too (previously only `backtest_signals.py` had it), so the same entry-indicator comparison already done on the signal-quality proxy could be redone against the real `ExitTrade` full lifecycle, isolating the entry-indicator variable under an otherwise identical exit-strategy config.
- Same window (4 weeks, `--start-pos 1`), same fixed pullback gate, same exit config (`EXIT_POST_BE_LOSS_CAP_MONEY=1.0`, `pct60_floor2` trailing) for both runs — `--full-lifecycle --mtf-entry-indicator {macd,sma}`.

## Results

| Entry | Trades | Total P&L | Win rate | P&L/trade |
|---|---|---|---|---|
| MACD | 124 | **-$21.60** | 31.5% | **-$0.174** |
| SMA | 403 | **-$91.60** | 28.8% | **-$0.227** |

Both net-losing. **MACD loses less, both in total and per-trade**, despite scoring worse than SMA on the signal-quality proxy for this exact window (33.6% vs 39.5%).

By real exit reason (both indicators show the same shape, proportionally):

| Reason | MACD (124 trades) | SMA (403 trades) |
|---|---|---|
| `profit_drop_after_be` | 73 (58.9%), avg -$1.13, total -$82.80 | 250 (62.0%), avg -$1.14, total -$284.20 |
| `trailing_breach_pct_of_peak` | 38 (30.6%), avg +$1.63, total +$61.80 | 108 (26.8%), avg +$1.71, total +$184.60 |
| `failed_to_reach_be` | 12 (9.7%), avg -$1.83, total -$22.00 | 33 (8.2%), avg -$1.56, total -$51.60 |
| `exhausted` | 1 (0.8%), avg +$21.40 | 8 (2.0%), avg +$10.37, total +$83.00 |
| `profit_drop` | 0 | 4 (1.0%), avg -$5.85, total -$23.40 |

## Findings

1. **The signal-quality proxy (fixed target/stop win rate) does not reliably predict real full-lifecycle P&L ranking between entry indicators.** SMA "won" the proxy comparison in this exact window (39.5% vs MACD's 33.6%) but "loses" the real comparison (-$0.227/trade vs MACD's -$0.174/trade). These are different questions: the proxy asks "how often does price move 8 pips before 5 pips against it," the real system asks "how does a tick-driven breakeven-then-trail-or-cap exit actually resolve" — and apparently these can disagree about which entry timing is better.
2. **Both indicators are net-losing under the current exit-strategy config on this window.** This matches the standing finding in `full-lifecycle-backtest.md` ("the system is still net negative... even at the best-tested [cap] setting") — this session's numbers are a fresh data point confirming that conclusion still holds, now under the fixed pullback gate too.
3. **The dominant loss driver is identical in shape for both indicators**: `profit_drop_after_be` (a trade reaches breakeven, then gets cut by the post-BE cap/trail for a small loss) accounts for ~59-62% of all trades and roughly 3x the losses of `trailing_breach_pct_of_peak`'s gains. This looks like a property of the exit-strategy's post-BE phase, not of either entry indicator specifically.
4. **This is a single window for each indicator** — exactly the situation that produced a wrong-turning-out single-window read for the signal-quality-proxy question earlier in this same investigation (MACD 33.6% window1 / 43.7% window2). There is no reason to assume this real-P&L comparison is more stable across windows than that one was; it hasn't been checked.

## Recommendation — do not change `Config.MTF_ENTRY_INDICATOR` again yet

This is a genuinely surprising, consequential result that contradicts the basis for this session's earlier MACD→SMA switch. Given this investigation's own established discipline (never trust a single window), the right next step is a second-window full-lifecycle comparison for both indicators before making any further change to live `Config` — not an immediate revert back to MACD on the strength of one window, and not leaving SMA in place on the strength of a proxy metric that this same session just showed can point the wrong way. Left for the user's decision: run the second window, revert to MACD now on the balance of evidence so far, or something else.
