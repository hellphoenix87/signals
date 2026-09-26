# Entry Timing, the Signal Lab, and the M5 RSI-Fade System (Tests P1, P1b, P2)

Date: 2026-09-26. Plan: [entry-timing.md](../plans/done/entry-timing.md). Pre-registrations: Tests
P1, P1b, P2 in [pre-registrations-2026-09-22.md](pre-registrations-2026-09-22.md).

**TL;DR**

- **Entry timing is not the lever at M1.** At the zero-spread entry, none of 22 entry-state features
  predicts whether a trade reaches the staircase (AUC 0.49-0.51). The live exit is a barrier game
  (+1 pip before -0.5 pip); a random walk wins it 33.3%, the live MACD signal 33.2%.
- **At M1 scale every simple signal's edge (~0.02-0.03 pip) is 3-4x smaller than friction
  (~0.09 pip fill slippage).** No target/stop shape between 0.5 and 1 pip fixes that.
- **Mean-reversion signals' edge grows with horizon faster than friction.** Best: RSI(14) fade on M5,
  first entry per episode, fixed +/-5 pips.
- **Test P2 (production code, all 24 unseen windows Mar 2021 - Jan 2023): PASS.** 945 trades, 53.9%
  win, **+$0.745/trade, +$704, t = 2.25**, vs the live config's +$601 on the same windows. The live
  config was also profitable in that period, so the case is risk, not return: half the window-to-window
  volatility, half the drawdown, 17 vs 13 positive windows, 48x fewer trades.
- Everything is built behind Config flags, **all off**. Turning it on is a user decision; a demo run
  should come first.

## 1. Entry timing at M1 (spent windows, exploratory)

18,744 live-config trades, 14 windows, entry tick reconstructed (99.5-100% price match):

- 22 features measured at the actual entry (price path during the wait / last 5 ticks / 60 s / 5 min,
  signal-candle shape, prior candles, tick activity, flat ticks, ATR, 1h range/position/trend, MACD
  value, signal freshness, wait time, hour): all AUC 0.49-0.51 for reaching the staircase. Combined
  model on held-out windows: AUC 0.512.
- The earlier "don't chase" / "quiet market" effects were about winning back the spread and vanish at
  a zero-spread entry.
- Barrier game at every real entry tick: signal 33.2%, opposite side 30.6%, random walk 33.3%,
  break-even with live fills 34.8%. Inverted-signal production runs (4 windows): signal -$0.023/trade,
  inverted -$0.153 -- the direction is real, but it only lifts the entry to random-walk level.

## 2. Signal lab (`scripts/signal_lab.py`)

Per spent window, every live-hour candle close gets its live entry tick (zero-spread wait) and the
barrier outcome for both directions; any rule is then scored by lookup against the same-tick random
direction. Screens, not results (95.8% agreement with the full backtest on live trades).

- **Rules at the live geometry** (+1/-0.5 pip, 14 windows): no rule reached the +3 pp bar. Every
  mean-reversion rule positive (Bollinger fade +1.80 pp, RSI fade +1.60, MACD against the 4h trend
  +1.69, live MACD +1.32), every momentum/trend rule negative (-0.4 to -1.8 pp).
- **Geometry** (+/-0.5..1 pip, 4 signals): all 20 combinations net negative. A random direction misses
  random-walk break-even in every geometry by 1.3-2.6 pp -- a fixed entry handicap (the spread reopens
  after a zero-spread entry), not reversion favouring one shape. Signal edge ~+0.02-0.03 pip vs fill
  slippage ~0.07-0.09 pip.
- **Scale** (+/-1, 2, 3, 5 pips; M1 and M5 signals): random-direction friction grows with scale
  (-0.10 -> -0.25 pip net), reversion edge grows faster. RSI fade M5: +4.7 -> +7.3 pp from +/-1 to
  +/-5. Robustness: 1,248 entries were 445 episodes; first entry per episode, +/-5: +7.9 pp, +0.51
  pip net, 8/12 windows (below the 75% bar set for that scan).

## 3. Screens on unseen data

| Test | Windows | Trades | Win % | Net pips/trade | Windows + | Verdict |
|---|---|---|---|---|---|---|
| P1 | W25-W30 (Jun-Nov 2024) | 51 | 60.8 | +1.02 | 1/1 counted | INCONCLUSIVE (wide-spread period, zero-spread entry never fired) |
| P1b | W43-W48 (Jan-Jul 2023) | 281 | 53.4 | +0.275 | 4/6 | PASS (weak, ~1.5 SE) |

## 4. The production build (all flags off by default)

| Config | Effect |
|---|---|
| `ENTRY_STRATEGY = "rsi_fade"` | `RsiFadeSignalStrategy`: Wilder RSI(14) < 30 buy / > 70 sell, first candle of each run only; stateless |
| `TF_ENTRY = M5` | entry timeframe (spread wait and session filter follow it) |
| `EXIT_FIXED_PIPS_ENABLED = True` (+`EXIT_FIXED_TARGET_PIPS`, `EXIT_FIXED_STOP_PIPS` = 5) | `FixedPipExitManager` replaces BE arming, cap and staircase; broker SL/TP set to the same levels as a backstop; lot sizing follows the 5-pip stop |
| `MAX_OPEN_POSITIONS_PER_SYMBOL = 1` | executor skips a signal while a position is open |

Backtest: `--rsi-fade-mode` drives all of it through production objects (`--allow-overlap` to disable
the one-position rule). Acceptance on spent W42: 52 trades / 61.5% / +1.14 pip vs the lab's
50 / 62.0% / +1.14. Wilder RSI on >= 200 candles matches long history exactly; live keeps 203; the
strategy holds below 100.

## 5. Test P2: production system on all 24 unseen windows

Production code, live session filter, 15 s zero-spread wait, 0.15-pip gate. Stages 8 -> 4 -> 8 -> 4
with early stop on negative pooled $/trade (never triggered: +0.283 -> +0.563 -> +0.591 -> +0.745).

| Pooled | Trades | Win % | $/trade | Total | t |
|---|---|---|---|---|---|
| **RSI-fade M5** | 945 | 53.9 | **+0.745** | **+$704.20** | **+2.25** |
| Live config | 45,051 | 34.9 | +0.013 | +$601.00 | +1.54 |

PASS: $/trade > 0, t >= 2, total > live.

| Risk (23 trading windows) | RSI-fade M5 | Live config |
|---|---|---|
| Windows positive | 17 | 13 |
| Window-total sd | $69 | $127 |
| Max drawdown | -$429 | -$806 |
| Longest losing streak | 7 trades | 41 trades |
| Largest single loss | -$19.00 | -$23.20 |
| 2021 / 2022 | +$27 / +$677 | -$177 / +$778 |

Window-total correlation between the two: 0.42.

<details><summary>Per window</summary>

| Window | Start | RSI n | RSI win % | RSI $/tr | RSI total | LIVE n | LIVE $/tr | LIVE total |
|---|---|---|---|---|---|---|---|---|
| W49 | 2022-12-20 | 19 | 57.9 | +1.547 | +29.4 | 1,097 | -0.108 | -118.8 |
| W50 | 2022-11-22 | 35 | 57.1 | +1.531 | +53.6 | 1,564 | -0.026 | -40.6 |
| W51 | 2022-10-25 | 40 | 57.5 | +1.605 | +64.2 | 1,482 | +0.036 | +53.6 |
| W52 | 2022-09-27 | 18 | 66.7 | +3.478 | +62.6 | 792 | +0.207 | +164.0 |
| W53 | 2022-08-30 | 43 | 62.8 | +2.642 | +113.6 | 1,522 | -0.009 | -13.0 |
| W54 | 2022-08-02 | 47 | 68.1 | +3.587 | +168.6 | 1,580 | +0.121 | +190.6 |
| W55 | 2022-07-05 | 37 | 56.8 | +1.286 | +47.6 | 1,578 | +0.081 | +128.0 |
| W56 | 2022-06-07 | 37 | 56.8 | +1.384 | +51.2 | 1,500 | +0.036 | +54.2 |
| W57 | 2022-05-10 | 41 | 61.0 | +1.980 | +81.2 | 1,454 | +0.089 | +129.8 |
| W58 | 2022-04-12 | 52 | 51.9 | +0.288 | +15.0 | 2,247 | -0.027 | -61.6 |
| W59 | 2022-03-15 | 45 | 60.0 | +1.964 | +88.4 | 2,506 | +0.074 | +185.8 |
| W60 | 2022-02-15 | 33 | 54.5 | +1.048 | +34.6 | 1,622 | +0.016 | +25.4 |
| W61 | 2022-01-18 | 57 | 38.6 | -2.333 | -133.0 | 2,643 | +0.031 | +80.8 |
| W62 | 2021-12-21 | 15 | 40.0 | -2.000 | -30.0 | 924 | -0.001 | -1.2 |
| W63 | 2021-11-23 | 51 | 43.1 | -1.392 | -71.0 | 2,592 | -0.075 | -193.8 |
| W64 | 2021-10-26 | 32 | 43.8 | -1.200 | -38.4 | 1,716 | -0.081 | -138.6 |
| W65 | 2021-09-28 | 49 | 42.9 | -1.527 | -74.8 | 2,399 | -0.068 | -162.6 |
| W66 | 2021-08-31 | 61 | 52.5 | +0.430 | +26.2 | 2,729 | -0.060 | -163.6 |
| W67 | 2021-08-03 | 37 | 62.2 | +2.432 | +90.0 | 2,156 | +0.010 | +21.2 |
| W68 | 2021-07-06 | 47 | 59.6 | +1.872 | +88.0 | 2,691 | -0.011 | -29.0 |
| W69 | 2021-06-08 | 54 | 55.6 | +1.052 | +56.8 | 3,241 | +0.046 | +148.6 |
| W70 | 2021-05-11 | 60 | 51.7 | +0.250 | +15.0 | 3,148 | +0.083 | +262.6 |
| W71 | 2021-04-13 | 35 | 45.7 | -0.989 | -34.6 | 1,868 | +0.042 | +79.2 |
| W72 | 2021-03-16 | 0 | -- | -- | 0.0 | 0 | -- | 0.0 |

</details>

## 6. How to read this

- **It passed, narrowly on the return criterion.** +$704 vs +$601 is not a large margin. The real
  difference is risk: similar money with half the volatility and drawdown and far shorter losing
  streaks.
- **2021-2022 was kind to both systems.** The live config, which loses about -$0.044/trade on
  2023-2026 data, made money here. On more recent data the RSI system's evidence is the screens (P1b
  2023 H1 +0.275 pip/trade; spent 2023-2026 lab windows +0.51 pip) and the W42 acceptance run -- all
  positive, none as strong as P2.
- **Different trade profile.** ~40 trades per four weeks instead of ~1,900, each risking ~$10 at
  0.2 lots (5-pip stop at 1% risk on the $1,000 sizing basis), held for hours. Far less dependent on
  tick-level execution; more exposed to single gaps (largest loss -$19, a 9.5-pip gap).
- **One pre-registered test.** The hypothesis came out of a search over ~30 rule/geometry
  combinations on spent data; P2's windows were unseen, so its t is fair for this rule, but it is one
  test at t = 2.25, not a proof.

## 7. Before switching it on

- Flags: `ENTRY_STRATEGY = "rsi_fade"`, `TF_ENTRY = M5`, `EXIT_FIXED_PIPS_ENABLED = True`,
  `MAX_OPEN_POSITIONS_PER_SYMBOL = 1` (the spread wait and session filter stay as they are).
- **Run it on the demo account first.** Neither the live spread-wait path (PR #76) nor the new M5 /
  fixed-pip / one-position path has run inside the app yet; the backtest and REPL checks do not
  exercise the orchestrator end to end.
- Unseen data left: none from 2021-03 onward for this hypothesis. Further evidence has to come from
  forward (demo or live) results.

## Artifacts

Test P2 per-trade CSVs: `backtest_results/EURUSD_full_lifecycle_20260926_*.csv`, one per window and
arm (logs name each file). Lab: `scripts/signal_lab.py` (`--geometry`, `--scale`, `--p1-screen`,
`--p1b-screen`).
