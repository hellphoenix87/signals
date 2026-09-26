# Signal quality and entry timing

Status: in-progress (branch `entry-timing`)

## Goal

With the live config (0.1-pip gate + 15 s zero-spread wait) no trade loses before breakeven, and the
outcome is near-binary: ~-$1.18 (the $1 cap) or ~+$2.22 (the staircase). The system breaks even when
**34.8%** of trades reach the staircase; it sits at **33.5%**. Exit tightening is exhausted
(docs/test-results/exit-tuning-limits-and-entry-focus.md). Find an entry-side change -- which
signals to take, or when exactly to enter -- that raises the staircase-reach share without cutting
the trade count so far that the total gets worse.

## Out of scope

- Exit settings ($1 cap, $2 staircase, 30-tick arming): held fixed at live values.
- Spread handling: the 15 s zero-spread wait and 0.1-pip gate stay as they are.
- Symbols other than EURUSD.

## Metrics

Per trade, from the full-lifecycle CSV: **reach** = `post_be_peak_profit >= 2` (got to the staircase);
**dud** = peak <= $0.20 (never followed through). Report reach %, dud %, $/trade, total, trades kept.
A candidate must raise reach % AND improve $/trade; total must not get worse.

## Phases

### Phase 1: Exploration on spent windows (no code in app/)

#### Subphase 1.1: Entry-state features for live-config trades

- Change: scratch analysis only. For the 14 live-config runs (Test O WAIT arm W31-W42 + W1/W2 base,
  `post_be_peak_profit` logged), reconstruct each trade's actual entry tick (signal candle close +
  `spread_wait_seconds`) and compute, using only information available at entry: signed price move
  during the wait, over the last 5 ticks / 60 s / 5 min; signal-candle shape (range, signed body,
  close position); prior 3-candle move signed; tick activity and flat-tick share; 1h range and
  position; spread-wait seconds; signal freshness (minutes since the previous signal, same or
  opposite direction); UTC hour.
- Acceptance: a table per feature of reach % / dud % / $/trade by quintile and the number of
  windows that agree on direction; reconstructed entry price matches the logged one on >= 99% of
  trades.

#### Subphase 1.2: Size the directional edge under live exits (E3)

- Change: `--invert-signal` runs on 2-4 spent windows with the live config, compared to the
  existing live runs.
- Acceptance: pooled $/trade and reach % for signal vs inverted signal, per window.

#### Subphase 1.3: Pick the hypothesis

- Change: write down the one or two candidate rules with the strongest, most consistent effect on
  reach %, and their expected trade-count cost. Recorded here; user picks before Phase 2.

### Phase 2: Implement the chosen rule in production

- To be specified after 1.3. Default shape for an entry-timing rule: a condition inside
  `SpreadWaitEntryStrategy` (it already holds the signal for up to 15 s on every tick), toggled by a
  Config flag and a backtest `--flag on|off` that drives the real wrapper. A signal-selection rule
  would be a new wrapper in `strategy_factory` in the same style.

### Phase 3: Pre-registered staged test (Test P)

- Unseen windows (ticks verified): W25-W30 (2024-06-04 .. 2024-11-19) and W43-W48
  (2023-01-17 .. 2023-07-04). Stages 4 -> 2 -> 4 -> 2 with order fixed at pre-registration, cumulative
  bars >= 3/4, >= 5/6, >= 8/10, >= 9/12, each also requiring pooled $/trade better and total no
  worse than the live config. Fail at any stage -> stop.

### Phase 4: Write-up and PR (not merged until the user decides)

## Open questions

- Which candidate goes to Phase 2 -- decided by the user after 1.3.

## QA

