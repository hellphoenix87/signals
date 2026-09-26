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

User choice (2026-09-26): **signal lab** -- screen directional entry rules with a fast barrier-game
screener (`scripts/signal_lab.py`) before any full backtest. Pass bar fixed before running: edge >=
+3 pp over the same-tick random direction pooled, positive in >= 10/12 windows with >= 100 trades,
and better than the live MACD.

Result (14 spent windows, live session hours, zero-spread entry, +1 / -0.5 pip barrier): **no rule
passes.** Best: Bollinger fade +1.80 pp (7/10), live MACD against the 4h trend +1.69 (8/11), RSI fade
+1.60 (8/10), breakout-60 fade +1.37 (7/10); live MACD +1.32 (9/12, the most consistent). Every
mean-reversion rule is positive and every momentum/trend rule negative (trend-following -0.4 to
-0.7, breakout/RSI/Bollinger follow -1.1 to -1.8). Best absolute win rate 34.1% vs break-even ~34.8%.
Reading: simple directional rules top out around +2 pp; the edge that exists is REVERSION, while the
+1 / -0.5 pip exit geometry asks for continuation. Next question (user decides): exit geometry for a
reversion signal.

User choice (2026-09-26): **geometry scan** -- `scripts/signal_lab.py --geometry`, live MACD + the
3 best reversion rules + random, target/stop +1/-0.5, +0.5/-0.5, +0.5/-1, +0.75/-0.75, +1/-1 pip;
net = after measured fill slippage (stops -0.09 pip, targets -0.04 pip). Bar fixed before running:
net > 0 pooled and positive in >= 10/12 windows.

Result: **no combination passes** -- all 20 net negative pooled, none positive in more than 2 windows.
Gross EV is ~0 (+/-0.01 pip) for every signal in every geometry. Correction to the reading above: a
random direction misses random-walk break-even in EVERY geometry by 1.3-2.6 pp (gross -0.018 to
-0.038 pip), including +0.5/-1, so it is not reversion favouring one shape -- it is a fixed entry
handicap (zero-spread entry, spread reopens next tick). The signals' direction is worth ~+0.02-0.03
pip/trade, just offsetting that; fill slippage (~0.07-0.09 pip) is 3-4x the edge. (Proxy is
pessimistic in absolute terms -- it ignores staircase tiers above $2; compare rows, not live P&L.)
Only remaining lever on this evidence: SCALE (larger targets/stops, M5/M15 entries), where fixed pip
frictions shrink relative to the move -- if the edge grows with horizon.

User choice (2026-09-26): **scale scan** -- `scripts/signal_lab.py --scale`: M1 signals (live MACD,
Bollinger/RSI fade, MACD against 4h trend) and M5 signals (production MACD on M5, Bollinger/RSI fade,
fade last candle), symmetric barriers +/-1, 2, 3, 5 pips, same slippage model. Bar fixed before
running: net > 0 pooled and positive in >= 75% of windows (>= 100 trades M1, >= 50 M5).

Result: **nothing passes the bar**, but a coherent pattern. Random-direction friction grows with
scale (net -0.10 pip at +/-1 -> -0.25 at +/-5: longer trades meet more spread spikes), yet the
reversion signals' edge grows faster: RSI fade on M5 +4.7 -> +7.3 pp from +/-1 to +/-5 (net +0.47
pip, 7/10 windows); RSI fade M1 +/-5 +4.5 pp (+0.22 pip, 5/10); MACD against 4h trend +/-5 +3.3 pp
(+0.12 pip, 7/11); production MACD on M5 +/-5 +3.3 pp (+0.10 pip, 5/10). Live MACD as-is stays ~+2 pp
at every scale; momentum rules ~0 or negative.

Robustness, RSI fade M5: 1,248 entries are 445 episodes (RSI stays extreme for several candles).
First entry per episode, +/-5 pips: win 55.7% (naive SE 2.4 pp), edge +7.87 pp, net +0.51 pip/trade
(~$1.02 at 0.2 lots); positive in 8/12 windows (67% < 75% bar); W1 -0.13, W2 +0.87. +/-3: +4.2 pp,
+0.10 pip. No unresolved barriers.

Candidate hypothesis (NOT passed): RSI(14) fade on M5 candles, first entry per episode, fixed +/-5 pip
target/stop, zero-spread entry, live session hours. A different system: ~$10 risk per trade at 0.2
lots (vs ~$1), hours-long trades, fixed pip exits production does not have. Proposed next: a
pre-registered lab screen on unseen W25-W30, keeping W43-W48 and 2022 unseen for any later
production test.

- Change: write down the one or two candidate rules with the strongest, most consistent effect on
  reach %, and their expected trade-count cost. Recorded here; user picks before Phase 2.

Screens: Test P1 (W25-W30) INCONCLUSIVE -- wide-spread period, 51 trades in 1 window. Test P1b
(W43-W48, tight spreads) **PASS (weak)**: 281 trades, 53.4% win, +4.45 pp, net +0.275 pip/trade,
4/6 windows. ~1.5 SE; ~+$26 expected per 4-week window at 0.2 lots vs ~$10 risk per trade.
Unseen and tight-spread for the production test: W49-W60 (2022). Reserve: 2021.

### Phase 2: Build the M5 RSI-fade system in production (user decision 2026-09-26: build, one position at a time)

All behind Config flags defaulting OFF -- the live bot is unchanged until the user flips them.

#### Subphase 2.1: Config tunables -- DONE
- `ENTRY_STRATEGY = "macd_vote"` (or `"rsi_fade"`), `RSI_FADE_PERIOD = 14`, `RSI_FADE_LOW = 30`,
  `RSI_FADE_HIGH = 70`; `EXIT_FIXED_PIPS_ENABLED = False`, `EXIT_FIXED_TARGET_PIPS = 5.0`,
  `EXIT_FIXED_STOP_PIPS = 5.0`; `MAX_OPEN_POSITIONS_PER_SYMBOL = None` (unlimited, as today).
  Running the mode also needs `TF_ENTRY = M5`.

#### Subphase 2.2: `RsiFadeSignalStrategy` -- DONE
- New base strategy: RSI(period) with Wilder smoothing on closes; RSI < low -> buy, > high -> sell,
  and only on the first candle of a run (previous candle's raw signal differs) -- stateless, computed
  from the candle list, so live and backtest agree. `strategy_factory` uses it instead of the MACD
  vote when `ENTRY_STRATEGY == "rsi_fade"`; spread wait and session filter wrap it as today.

#### Subphase 2.3: Fixed pip exits -- DONE
- New `FixedPipExitManager.check_exit_on_tick`: exit at +target / -stop pips from entry (bid for
  buys, ask for sells), reasons `fixed_target` / `fixed_stop`. `ExitTradeConfig` gains
  `fixed_pips_enabled`, `fixed_target_pips`, `fixed_stop_pips`; when enabled `ExitTrade.on_tick` uses
  only this manager (no BE arming, cap or staircase). `TradeExecutor` then sets the broker-side SL/TP
  to the same pips as a backstop (instead of DEFAULT_SL/TP_PIPS).

#### Subphase 2.4: One position at a time -- DONE
- `TradeExecutor.execute_signals` skips a signal when `MAX_OPEN_POSITIONS_PER_SYMBOL` is set and the
  symbol already has that many open positions.

#### Subphase 2.5: Backtest support -- DONE
- `--rsi-fade-mode`: sets `TF_ENTRY = M5`, `ENTRY_STRATEGY = "rsi_fade"`, fixed-pip exits and one
  position per symbol, all through the production objects; the full-lifecycle loop uses
  `exit_trade._fixed_manager` when present; one-position is mirrored by skipping signals whose entry
  falls before the previous simulated trade's exit tick.
- Acceptance: on a spent window, with one-position OFF, trade count and win rate match the lab's M5
  RSI-fade first-per-episode +/-5 numbers closely; with it ON, fewer trades, no overlaps.

Phase 2 acceptance (W42, spent, production backtest `--rsi-fade-mode`): overlap allowed 52 trades,
61.5% win, +$118.20 (+1.14 pip/trade) vs the lab's 50 / 62.0% / +1.14 pip -- match. One position at a
time: 45 trades (7 skipped), 62.2%, +$107.80. Exits `fixed_target` avg +$10.19, `fixed_stop` avg
-$10.40 (slippage in line with the lab model). RSI history: live keeps 203 candles; Wilder RSI on
>= 200 matches long history exactly (strategy holds below 100).

### Phase 3: Test P2 -- production system, pooled over all 24 unseen windows

- User decision 2026-09-26: pooled grading over all unseen data (W49-W72, Mar 2021 - Jan 2023),
  stages 8 -> 4 -> 8 -> 4 with early stop on negative pooled $/trade; PASS = pooled $/trade > 0,
  t >= 2, and total better than the live config. Pre-registered as Test P2.

Stage 1 (W49, W52, W55, W58, W61, W64, W67, W70) RSI arm: 312 trades, 51.6% win, **+$0.283/trade**,
+$88.20, t = 0.49 -> no early stop. Per window -$133 (W61) .. +$90 (W67). LIVE arm pending.

### Phase 4: Write-up and PR (not merged until the user decides)

## Open questions

- Which candidate goes to Phase 2 -- decided by the user after 1.3.

## QA

