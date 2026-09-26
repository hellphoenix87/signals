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

#### Subphase 1.1: Entry-state features for live-config trades -- DONE

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

Result 1.1 (18,744 live-config trades, 14 spent windows; entry tick reconstructed, price match
99.5-100%): **nothing observable at the zero-spread entry predicts reaching the staircase.** All 22
features AUC 0.49-0.51 (price path during the wait / 5 ticks / 60 s / 5 min, signal-candle shape,
prior 3 candles, tick activity, flat ticks, ATR, 1h range/position/trend, MACD histogram, signal
freshness/run length, wait seconds, hour). Combined model, leave-one-window-out: AUC 0.512; keeping its
top 60% moves reach 33.6% -> 34.2%. The earlier "don't chase" / "quiet market" effects (AUC 0.53-0.56)
were about winning back the spread and vanish at a zero-spread entry.

Why: the exit geometry is a barrier game. At 0.2 lots 1 pip = $2, so reaching the staircase = +1 pip
before the -0.5 pip ($1) cap. A driftless random walk wins that 0.5/1.5 = **33.3%**; the live config
reaches **33.5%**. Played at every real entry tick (tick data, 95.8% agreement with the backtest):

| Direction at the same entry tick | +1 pip before -0.5 pip |
|---|---|
| Signal | **33.2%** |
| Opposite | 30.6% |
| Random walk | 33.3% |
| Break-even with live fills | 34.8% |

The signal beats the opposite side by +2.6 pp pooled (std error ~0.34 pp), but only in 7 of 12
windows (W1/W2 +4-5 pp; several 2023-24 windows negative). Relative to a random direction (the
average of both sides, ~31.9%) the signal adds ~+1.3 pp; break-even needs ~+2.9 pp. Both sides sit
below the random walk: EURUSD is mean-reverting at the half-pip scale, which penalises any entry.

#### Subphase 1.2: Size the directional edge under live exits (E3) -- DONE

- Change: `--invert-signal` runs on 2-4 spent windows with the live config, compared to the
  existing live runs.
- Acceptance: pooled $/trade and reach % for signal vs inverted signal, per window.

Result 1.2 (live config, production backtest, `--invert-signal`, 4 spent tight-spread windows):

| Window | Signal $/tr | Inverted $/tr | Signal reach % | Inverted reach % |
|---|---|---|---|---|
| W1 | -0.019 | -0.210 | 34.4 | 28.6 |
| W2 | -0.050 | -0.195 | 34.1 | 29.6 |
| W40 | +0.005 | -0.034 | 34.1 | 32.6 |
| W41 | -0.004 | -0.057 | 34.5 | 31.2 |
| **Pooled (10,724 trades each)** | **-0.023** | **-0.153** | **34.2** | **30.0** |

The signal's direction is real (4/4 windows, +$0.13/trade over its inverse), consistent with the
barrier game. But it lifts the entry from the market's half-pip mean-reverting baseline (random
direction ~ -$0.09/trade) only to about random-walk level, not past the 34.8% break-even.

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

