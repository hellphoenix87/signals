# Exit Strategy: Reactive Re-entry, Staircase Trail, and Regime Filters

Date: 2026-09-21

**TL;DR**: One day of full-lifecycle exit-strategy experiments on EURUSD. Two findings held up: the entry signal's *direction* has a real edge, and a `$2` staircase trail beats the live percentage-of-peak trail. Everything else failed out of sample: re-entry after a cap hit, hedging, wide/disabled loss caps, and volatility-bucket regime rules. Each looked strong on the windows it was found in and broke on the next one. **No `Config` change was made.** The system is still net negative on every window at the live `$1` cap.

## Setup

- **Tool**: `scripts/backtest_exit_strategy.py --full-lifecycle`: replays real MT5 ticks through the real `ExitTrade` (`LossExitManager` + `ProfitExitManager`, real precedence) from entry to final exit. All modes below were added this session and are backtest-only.
- **Windows** (4-week, `--start-pos`, live pull so dates drift slightly between runs):
  - W1 = `1` (~Aug 25 – Sep 22)
  - W2 = `28801` (~Jul 28 – Aug 25)
  - W3 = `57601` (~Jun 30 – Jul 28)
  - W4 = `86401` (**partial: Jun 15 – Jun 30 only**; M1 candle history ends Jun 15 on this account, while ticks go back to at least May 20. Probably MT5's "Max bars in chart" setting.)
- **Tick budget**: `--full-lifecycle-max-ticks 10000`/`20000` for all trail/cap comparisons. The default `4000` produced an `exhausted` bucket (trades still open at cutoff, marked at an arbitrary price) that was **systematically optimistic** (avg +$10–16/trade) and distorted comparisons. Treat older 4,000-tick full-lifecycle numbers with caution.

## New tooling (all in `scripts/backtest_exit_strategy.py`)

| Flag | What it does |
|---|---|
| `--single-timeframe` / `--indicators` | Run the single-timeframe (M1 MACD+SMA+RSI vote) strategy through the real exit system; previously hardcoded to MTF |
| `--chain-on-cap` (+ `--chain-flip-direction`, `--chain-max-legs`, `--chain-loss-cap`) | On a `-$1` post-BE cap hit, immediately open a new leg (same or reversed direction) with fresh BE/trail/cap state |
| `--hedge-on-cap` (+ `--original-loss-cap`) | On a cap hit, keep the original open and add an opposite-direction hedge leg |
| `--staircase-trail` (+ `--staircase-tier-width`, `--staircase-first-tier`) | Replace the percentage-of-peak trail with a ratchet: each tier crossed becomes the new stop |
| `--sweep-staircase-tiers` / `--sweep-staircase-cap` | One-pass sweeps of tier width / post-BE cap with the staircase trail |
| Per-trade CSV columns | `num_indicators_agree`, `indicator_votes`, `rsi_value`, `macd_hist_value`, `atr_value`, `cf_recovered_to_neg1` |

## Findings that held up

1. **The loss cap, not the trail, is where the money is lost.** Single-timeframe W1/W2: cap exits = ~82–85% of real losses (-$2,856 / -$2,767); the trail is net positive (+$1,540 / +$1,468).
2. **The percentage trail gives back ~70% of every winning move.** Trail exits reached avg peak $4.28–4.31 but captured only $1.40–1.41 (29.6–29.8%) in both windows.
3. **`$2` staircase trail beats the live 60%/$2 trail** (single-timeframe, live `$1` cap, 10k ticks): W1 -$1,048 → -$647, W2 -$681 → -$630. It captures the same population (peak > $2) more efficiently and ~2.4× faster (~340 vs ~820 ticks). **Only validated on W1–W2**; confirm on W3/W4 at the live cap before relying on it.
4. **Earlier trail activation is worse.** First tier at $0.5 (step $2): W1 -$646 → -$846, W2 -$630 → -$789, despite win rate rising to ~57–60%. Same lesson as the `$0`-cap and unified-stop tests: reacting to small early peaks trades away bigger later moves.
5. **The signal's direction has a real edge.** Normal minus inverted (`--invert-signal`), same exits: +$740 / +$616 / +$886 at the `$1` cap in W1/W2/W3, positive at every cap tested. (Not checked on W4.)
6. **Single-timeframe ≈ MTF per trade**, just ~30× more trades: -$0.14 to -$0.24/trade vs MTF -$0.19 to -$0.26. Entry frequency and selectivity don't change the per-trade edge.

## Ruled out (failed on the next window)

| Idea | Looked good on | Failed on |
|---|---|---|
| Re-enter same direction after cap hit | — | W1/W2 (3–4× worse, monotonic in leg count) |
| Reverse direction after cap hit (unlimited / single flip / flip with `$3` or BE cap) | W1 single flip (+$3.80) | W2 (-$79); flip fails 66–73% of the time after arming BE |
| Hedge instead of close (original uncapped / `$5` / `$10`) | W2 uncapped (+$11) | W1 (-$166); windows want opposite cap widths |
| Wide / disabled post-BE cap with staircase trail | W1–W2 (+$1,124 / +$3,209 at `$50`, monotonic) | **W3 (-$595) and W4 (-$1,350)** |
| Skip calm entries (ATR < 0.71 pips) + `$50` cap | W1–W3 (all positive) | **W4 (-$1,456), rule fixed in advance.** Bucket pattern flipped. |
| Entry-time predictors of reversal: confidence, agreement, ADX, RSI extremity, MACD histogram, ATR | — | Correlation ≈ 0 at n ≈ 4,000/window |

- **Single-timeframe `confidence` is degenerate:** with weights MACD 1 / SMA 1 / RSI 2.5 and threshold 0.5, ~98% of signals fire on RSI alone (MACD+SMA can't reach threshold without RSI).
- **Tier-width sweep** (`$0.5`–`$5`) peaked at `$2` in W1 and `$4` in W2. `$2`–`$4` all beat both older trails in W1–W2, but the exact optimum doesn't reproduce.

## Regime check (window-level, hindsight only)

| | W1 | W2 | W3 |
|---|---|---|---|
| M1 ATR mean (pips) | 0.97 | 1.15 | 1.30 |
| M15 ADX mean / % > 25 | 24.3 / 41% | 23.7 / 35% | 26.4 / 52% |
| Net drift / total range (pips) | -193 / 225 | +289 / 358 | -55 / 121 |

W3 was the most volatile intraday but went nowhere over the month. That fits "the exits bled in both directions there," but it's only visible in hindsight. The per-trade version (ATR buckets) didn't reproduce on W4.

## Takeaways for the next session

- **Treat any exit-parameter result found and judged on the same windows as unconfirmed.** Today every such result failed out of sample. Fix the rule and thresholds first, then test on unseen data. Forward testing on the demo account is the only fully fit-proof check.
- **Most promising open item**: confirm the `$2` staircase trail vs. the live percentage trail at the live `$1` cap on W3/W4. If it holds, it's the one change here worth considering for `ProfitExitManager`.
- **Untested entry-time context** (genuinely different information from M1 price indicators): H4/daily trend, position within the day's range, session/time-of-day vs. post-BE reversal, economic-calendar events.
- **Unblock more history**: raise MT5 "Max bars in chart" to get full W4/W5 M1 data.
