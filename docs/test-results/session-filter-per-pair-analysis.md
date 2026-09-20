# Session Filter Per-Pair Analysis

Date: 2026-09-20

## Setup

- **Pairs**: the same 7 majors used throughout the post-breakeven investigation -- `EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD NZDUSD`.
- **Windows**: the same two independent, non-overlapping 4-week M1 windows `session-filter-analysis.md` used (`--start-pos 1` and `--start-pos 28801`).
- **Method**: same as the original EURUSD-only analysis -- MTF replay, each signal's UTC hour recovered from its candle timestamp via `get_broker_utc_offset_hours` (offset 5, at analysis time). Ratio/spread: `Config`'s current live defaults (target=8/stop=5) with a realistic 1-pip round-trip spread, no `--session-filter` override.
- **Bug found and fixed mid-analysis**: the first sweep pass was run without any session-filter CLI override, but `Config.USE_SESSION_FILTER` already defaults to `True` in production -- so that pass silently replayed *with* the live filter on, zeroing out every signal in 08:00-18:59 UTC and defeating the entire point of an unfiltered hour-by-hour reading. Added `--no-session-filter` to `scripts/backtest_signals.py` to force it off for this kind of discovery pass, and reran all 14 combinations.
- **Commands**:
  ```
  PYTHONPATH=. pipenv run python scripts/backtest_signals.py --symbol <PAIR> --mtf --weeks 4 --spread-pips 1 --start-pos <1|28801> --no-session-filter
  PYTHONPATH=. pipenv run python scripts/analyze_session_filter_by_pair.py <csvs...>
  ```

## Results: pooled (both windows), per pair

| Pair | n (decided) | In-block (08-18 UTC) win% | Outside-block win% | Gap (outside − inside) |
|---|---|---|---|---|
| AUDUSD | 624 | 29.3% (n=328) | 18.9% (n=296) | **−10.3 pts** |
| EURUSD | 547 | 25.3% (n=297) | 38.0% (n=250) | **+12.7 pts** |
| GBPUSD | 534 | 26.1% (n=326) | 29.8% (n=208) | +3.7 pts |
| NZDUSD | 633 | 29.2% (n=277) | 33.7% (n=356) | +4.5 pts |
| USDCAD | 615 | 31.7% (n=341) | 32.8% (n=274) | +1.2 pts |
| USDCHF | 563 | 22.7% (n=352) | 23.2% (n=211) | +0.5 pts |
| USDJPY | 1019 | 36.9% (n=531) | 29.7% (n=488) | **−7.2 pts** |

Full hour-by-hour tables (pooled across both windows) for all 7 pairs are in the per-signal CSVs referenced above (`backtest_results/*_mtf_nosessionfilter_*.csv`) and were printed in full during this analysis; not reproduced here in full, only the block-level roll-up, since (per the finding below) individual hours are noisy and only the block-level pattern is being relied on -- same caveat the original EURUSD analysis made.

## Results: per-window reproducibility check

Pooling alone can hide a result driven by one anomalous window (the original EURUSD analysis explicitly checked this). Same gap (outside − inside), computed separately per window:

| Pair | Window 1 gap | Window 2 gap | Reproducible? |
|---|---|---|---|
| EURUSD | +19.5 | +5.2 | **Yes** -- same direction, magnitude varies (matches the original analysis's own observation that EURUSD's effect size itself varies a lot between windows) |
| AUDUSD | −4.5 | −13.6 | **Yes** -- same direction, both reversed from EURUSD |
| USDJPY | −9.5 | −4.8 | **Yes** -- same direction, both reversed from EURUSD |
| GBPUSD | +3.9 | +5.1 | Yes, but small -- consistent direction, weak magnitude |
| NZDUSD | +5.2 | +3.5 | Yes, but small -- consistent direction, weak magnitude |
| USDCAD | +0.6 | +1.7 | Yes, but negligible -- no real effect either way |
| USDCHF | −8.0 | +9.5 | **No** -- windows disagree on direction entirely; noise, not signal |

## Findings

1. **EURUSD's own block is confirmed real and reproducible for EURUSD itself** -- both windows show a positive gap (08-18 UTC underperforms), consistent with `session-filter-analysis.md`'s original finding. This is a sanity check on the methodology, not new information.
2. **The block does not transfer to the other 6 majors.** Only EURUSD shows a gap of the same order of magnitude (+12.7 pts pooled, +19.5/+5.2 per window) as the original discovery. GBPUSD and NZDUSD show the same *direction* but at roughly a quarter to a third of EURUSD's magnitude -- too weak to justify treating them the same as EURUSD. USDCAD shows no effect at all. USDCHF's two windows actively disagree on direction -- noise.
3. **AUDUSD and USDJPY show a reproducible *reversed* effect** -- both windows, both pairs, the 08-18 UTC block is the *better*-performing block, not the worse one. Applying EURUSD's blocked-hours window to either pair would actively suppress its better signals, not its worse ones. This is the clearest, most decisive result in this analysis: session effects genuinely differ per pair in *direction*, not just magnitude, confirming the hypothesis raised when this thread was first written (different pairs, different dominant trading sessions).
4. **Individual hours are still too noisy to read directly** -- same caveat as the original EURUSD-only analysis (Finding 4 there). Only the block-level, cross-window-reproducible pattern is being relied on here.

## Answer to Thread 1's actual question

**Pooling the other 6 pairs with EURUSD to validate Thread 4's ticks-to-cap dynamic-cap signal is not justified.** Their session-driven behavior is demonstrably not comparable to EURUSD's -- two pairs (AUDUSD, USDJPY) show a reproducibly *opposite* session effect, one (USDCHF) shows no reproducible effect at all, and the remaining two positive-direction pairs (GBPUSD, NZDUSD) are far weaker than EURUSD, not a close enough match to treat as interchangeable. Thread 4's dynamic post-BE cap signal therefore stays blocked on EURUSD-only data (2 real-recovery trades, too few to confirm) until more EURUSD-only history accumulates -- pooling in the other 6 pairs is not a valid shortcut around that.

**No production change follows from this pass**: `Config.SYMBOLS` is EURUSD-only today, and EURUSD's own existing blocked-hours window is confirmed correct for EURUSD, so there is nothing to rewire. If/when a second pair is added to live trading, this data says its own session window would need to be independently derived first -- reusing EURUSD's 08-18 UTC block on AUDUSD or USDJPY specifically would be actively harmful, not just untested.
