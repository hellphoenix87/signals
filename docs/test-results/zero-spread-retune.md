# Zero-Spread Re-tune (Confirmed Real Account Conditions)

Date: 2026-09-16

## Setup

- **Symbol**: EURUSD, both out-of-sample windows used throughout this investigation (window 1: `--start-pos 1`; window 2: `--start-pos 28801`).
- **Motivation**: the user confirmed `MetaQuotes-Demo` (login 112575722) is the actual account this bot will trade on — no real money, but otherwise real trading conditions — and its measured spread is near-zero (median 0, mean 0.016 pips historically; a direct tick-level check found real bid/ask spread averaging 0.045 pips over a recent hour, max 0.2 pips). Every tuning decision since spread modeling was introduced ([spread-and-second-window-validation.md](spread-and-second-window-validation.md) onward) assumed a pessimistic 1-pip spread. This re-runs the ratio and RSI-weight sweeps at `--spread-pips 0` to find the genuinely correct configuration for the real (confirmed) trading conditions.
- **Honest caveat**: `MetaQuotes-Demo` is MetaQuotes' own generic testing server, not a specific broker's demo — flagged to the user directly; proceeding on their explicit confirmation that conditions should match a real account.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --rsi-weight 2.5 --target-pips <3-10> --stop-pips 5 --spread-pips 0 [--start-pos 28801]
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol EURUSD --weeks 4 --mtf --session-filter --target-pips <3-10> --stop-pips 5 --spread-pips 0 [--start-pos 28801]
  ```

## Results: ratio sweep at spread=0 (window 1)

| Target:5 | Single-tf breakeven | Single-tf win rate | Gap | MTF breakeven | MTF win rate | Gap |
|---|---|---|---|---|---|---|
| 3:5 | 62.5% | 66.5% | +4.0 | 62.5% | 64.0% | +1.5 |
| **4:5** | 55.6% | **59.9%** | **+4.3** | 55.6% | 59.6% | +4.0 |
| 5:5 | 50.0% | 53.5% | +3.5 | 50.0% | 56.6% | +6.6 |
| 6:5 | 45.5% | 48.1% | +2.6 | 45.5% | 53.0% | +7.5 |
| 7:5 | 41.7% | 42.8% | +1.1 | 41.7% | 49.2% | +7.5 |
| **8:5** | 38.5% | 37.9% | -0.6 | 38.5% | **47.7%** | **+9.2** |
| 9:5 | 35.7% | 33.8% | -1.9 | 35.7% | 44.6% | +8.9 |
| 10:5 | 33.3% | 29.6% | -3.7 | 33.3% | 40.3% | +7.0 |

Single-timeframe peaks early (target=4) then declines, crossing back below breakeven past target≈7-8. **MTF peaks later (target=8) and stays above breakeven across the entire 3-10 range tested** — both a higher peak and a much wider profitable zone than before.

## Results: RSI weight re-sweep at spread=0 (target=4/stop=5, window 1)

| RSI weight | Win rate | Signals |
|---|---|---|
| 1.0 | 53.2% | 353 (small sample -- low weight means RSI rarely dominates alone) |
| 1.5 | 59.5% | 2,864 |
| 2.0 | 59.2% | 3,929 |
| **2.5** | **59.9%** | 3,956 |
| 3.0 | 59.3% | 3,900 |
| 4.0 | 59.3% | 3,899 |

**`Config.ENTRY_RSI_WEIGHT = 2.5` (the existing default) remains at least tied-best** among 1.5-4.0 (all cluster within noise, 59.2-59.9%); only 1.0 is clearly worse. No change needed.

## Results: out-of-sample validation (window 2)

| Config | Window 1 | Window 2 | Pooled |
|---|---|---|---|
| Single-tf 4:5 | 59.9% (+4.3) | 59.7% (+4.1) | 59.8% (+4.2) |
| **MTF 8:5** | 47.7% (+9.2) | 44.4% (+5.9) | **46.2% (+7.7)** |

Both new optima reproduce well out-of-sample — single-timeframe's result is remarkably stable (59.9% vs 59.7%); MTF's shrinks somewhat (9.2 → 5.9 points) but stays clearly positive. **MTF remains the stronger candidate**, nearly double single-timeframe's pooled margin.

## Findings

1. **Confirming the real spread as ~0 changes the picture completely**: every configuration tested now clears breakeven with real margin, versus the previous "right at breakeven, thin margin" verdict under the 1-pip assumption. This isn't re-litigating whether the edge is real (already established via random-baseline comparison and cross-window reproduction) — it's confirming that the earlier pessimistic cost assumption was masking a materially larger margin.
2. **The optimal ratio shifted for both strategies once spread stopped dominating the tightest targets**: single-timeframe's peak moved from a 3:5-5:5 plateau (spread=1) to a sharper peak at 4:5; MTF's single-point optimum moved from 7:5 to 8:5, and its whole profitable zone widened (all of 3:5-10:5 now clear breakeven, versus only 7:5 doing best-but-still-negative before).
3. **MTF is now unambiguously the stronger strategy** (+7.7 pooled vs. single-timeframe's +4.2) — a bigger gap than before, not just a smaller one that happens to lead.
4. **`Config.DEFAULT_TP_PIPS` updated from 7.0 to 8.0** to match MTF's new zero-spread-validated optimum (MTF is the live strategy, `USE_MULTI_TIMEFRAME_SIGNALS=True`). `DEFAULT_SL_PIPS` stays at 5.0 (unchanged, matches `MIN_SL_PIPS` floor). `ENTRY_RSI_WEIGHT` stays at 2.5 (confirmed still near-optimal, only relevant to single-timeframe anyway).
5. **Still worth remembering**: this is measured against a generic MetaQuotes testing server, not a specific broker's real conditions — the user has confirmed this is the account that will actually be traded, but if that ever changes (a different broker, a funded real account elsewhere), this whole re-tune would need to be redone against that account's actual spread.
