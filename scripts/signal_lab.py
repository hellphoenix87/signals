"""Signal lab: fast screen of directional entry rules against the live exit geometry.

With the live config every trade enters at zero spread (15 s spread wait) and its fate is a
barrier game: reach the staircase (+$2 = +1 pip at 0.2 lots) before the post-BE cap
(-$1 = -0.5 pip). A driftless random walk wins that 33.3%; break-even with live fills is ~34.8%.
(docs/plans/in-progress/entry-timing.md, Phase 1.)

For each window, once:
  - every M1 candle close in the live session hours (DST-correct filter) gets its live entry tick:
    the first tick within SPREAD_WAIT_SECONDS whose spread is <= SPREAD_WAIT_MAX_POINTS, else none;
  - the barrier game is played from that tick for BOTH directions (buy at ask judged on bid,
    sell at bid judged on ask).
Any rule is then just a buy/sell/hold per candle, scored by lookup. The same-tick average of both
directions is the "random direction" baseline, so each rule is judged by its edge over random at
exactly the moments it trades.

This is a SCREEN, not a result: it approximates the exit (95.8% agreement with the full-lifecycle
backtest on live trades) and ignores the tiers above $2. Anything that passes must go through the
production full-lifecycle backtest and a pre-registered staged test.

Usage:
    PYTHONPATH=. pipenv run python scripts/signal_lab.py            # all default windows
"""

import datetime as dt
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from app.config.settings import Config
from app.data.market_data import MarketData
from app.signals.strategies.session_filtered_signal_strategy import SessionFilteredSignalStrategy

PIP = 0.0001
UP, DN = 1.0 * PIP, 0.5 * PIP          # staircase first tier ($2) / post-BE cap ($1) at 0.2 lots
WIN_PAYOFF, LOSS_PAYOFF = 2.216, 1.184  # measured average live fills (14 windows)
BREAKEVEN = LOSS_PAYOFF / (WIN_PAYOFF + LOSS_PAYOFF)
POINT = 0.00001
CHUNKS = (400, 4000, 40000)             # scan lengths for the first-passage search

WINDOWS = {  # spent windows only -- never screen on the unseen ones reserved for Test P
    "W1": "2026-08-25", "W2": "2026-07-28",
    "W31": "2024-05-07", "W32": "2024-04-09", "W33": "2024-03-12", "W34": "2024-02-13",
    "W35": "2024-01-16", "W36": "2023-12-19", "W37": "2023-11-21", "W38": "2023-10-24",
    "W39": "2023-09-26", "W40": "2023-08-29", "W41": "2023-08-01", "W42": "2023-07-04",
}

_SESSION = SessionFilteredSignalStrategy(None, [], lambda c: None,
                                         broker_timezone=getattr(Config, "BROKER_TIMEZONE", "Europe/Athens"))
BLOCKED = set(getattr(Config, "SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL", {}).get("EURUSD", []))
WAIT_S = float(getattr(Config, "SPREAD_WAIT_SECONDS", 15.0))
WAIT_MAX_PTS = float(getattr(Config, "SPREAD_WAIT_MAX_POINTS", 0.5))


def _ticks(start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    chunks, t = [], start
    while t < end:
        e = min(t + dt.timedelta(days=1), end)
        k = mt5.copy_ticks_range("EURUSD", t, e, mt5.COPY_TICKS_ALL)
        if k is not None and len(k):
            chunks.append(pd.DataFrame(k)[["time_msc", "bid", "ask"]])
        t = e
    return pd.concat(chunks).drop_duplicates().sort_values("time_msc").reset_index(drop=True)


def _first_passage(px: np.ndarray, start: int, entry: float, side: int) -> float:
    """1.0 if price reaches entry+UP (in trade direction) before entry-DN, 0.0 if the reverse,
    NaN if neither within the longest chunk."""
    for n in CHUNKS:
        seg = (px[start:start + n] - entry) * side
        up = np.flatnonzero(seg >= UP - 1e-9)
        dn = np.flatnonzero(seg <= -DN + 1e-9)
        u = up[0] if len(up) else None
        d = dn[0] if len(dn) else None
        if u is not None or d is not None:
            if d is None or (u is not None and u < d):
                return 1.0
            return 0.0
        if start + n >= len(px):
            break
    return np.nan


def build_window(md: MarketData, name: str, start_s: str) -> pd.DataFrame:
    """Per live-hours candle: OHLC history for rules + both-direction barrier outcomes."""
    start = dt.datetime.fromisoformat(start_s)
    end = start + dt.timedelta(days=28)
    candles = pd.DataFrame(md.get_candles_range("EURUSD", mt5.TIMEFRAME_M1, start - dt.timedelta(days=3), end))
    candles = candles.set_index("time").sort_index()
    k = _ticks(start, end + dt.timedelta(days=1))
    tm, bid, ask = k.time_msc.to_numpy(), k.bid.to_numpy(), k.ask.to_numpy()
    spread_pts = (ask - bid) / POINT

    c = candles.copy()
    c["in_window"] = c.index >= start
    c["live_hour"] = [(_SESSION._utc_hour(t.to_pydatetime()) not in BLOCKED) for t in c.index]
    close_ms = np.array([(t.to_pydatetime() + dt.timedelta(minutes=1)).timestamp() * 1000 for t in c.index], dtype="int64")
    a = np.searchsorted(tm, close_ms, side="left")
    b = np.searchsorted(tm, close_ms + int(WAIT_S * 1000), side="right")
    entry_idx = np.full(len(c), -1)
    for n in np.flatnonzero(c.in_window.to_numpy() & c.live_hour.to_numpy()):
        hit = np.flatnonzero(spread_pts[a[n]:b[n]] <= WAIT_MAX_PTS)
        if len(hit):
            entry_idx[n] = a[n] + hit[0]
    c["entry_idx"] = entry_idx
    ok = entry_idx >= 0
    buy = np.full(len(c), np.nan)
    sell = np.full(len(c), np.nan)
    for n in np.flatnonzero(ok):
        i = entry_idx[n]
        buy[n] = _first_passage(bid, i, ask[i], +1)
        sell[n] = _first_passage(ask, i, bid[i], -1)
    c["win_buy"], c["win_sell"] = buy, sell
    c["w"] = name
    return c


# ---- candidate rules: +1 buy, -1 sell, 0 hold, computed from candles up to and including i ----

def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def rules(c: pd.DataFrame) -> dict:
    cl, hi, lo = c.close, c.high, c.low
    out = {}
    for n in (20, 60, 240):
        out[f"trend_sma{n}"] = np.sign(cl - cl.rolling(n).mean())
    for n in (60, 240):
        out[f"trend_ret{n}"] = np.sign(cl - cl.shift(n))
    for k in (1, 3, 5):
        mv = (cl - cl.shift(k)) / PIP
        fade = np.where(mv >= 0.5, -1, np.where(mv <= -0.5, 1, 0))
        out[f"fade_last{k}"] = pd.Series(fade, index=c.index)
        out[f"follow_last{k}"] = -out[f"fade_last{k}"]
    r = _rsi(cl)
    out["rsi_fade"] = pd.Series(np.where(r < 30, 1, np.where(r > 70, -1, 0)), index=c.index)
    out["rsi_follow"] = -out["rsi_fade"]
    m, s = cl.rolling(20).mean(), cl.rolling(20).std()
    out["boll_fade"] = pd.Series(np.where(cl < m - 2 * s, 1, np.where(cl > m + 2 * s, -1, 0)), index=c.index)
    out["boll_follow"] = -out["boll_fade"]
    for n in (20, 60):
        hh, ll = hi.shift(1).rolling(n).max(), lo.shift(1).rolling(n).min()
        out[f"breakout{n}_follow"] = pd.Series(np.where(cl > hh, 1, np.where(cl < ll, -1, 0)), index=c.index)
        out[f"breakout{n}_fade"] = -out[f"breakout{n}_follow"]
    return {k: pd.Series(v, index=c.index).fillna(0).astype(int) for k, v in out.items()}


def score(frame: pd.DataFrame, direction: pd.Series) -> pd.DataFrame:
    """Per window: trades, win rate in the rule's direction, same-tick random-direction rate."""
    x = frame.assign(dirn=direction.reindex(frame.index).fillna(0).astype(int))
    x = x[(x.dirn != 0) & x.win_buy.notna() & x.win_sell.notna()]
    x = x.assign(win=np.where(x.dirn > 0, x.win_buy, x.win_sell), rand=(x.win_buy + x.win_sell) / 2)
    return x


def main() -> None:
    for _ in range(10):
        if mt5.initialize():
            break
        time.sleep(3)
    else:
        sys.exit("MT5 initialization failed")
    md = MarketData()
    live = pd.read_pickle(Path(sys.argv[1])) if len(sys.argv) > 1 else None  # optional live-signal (w, time, direction)
    frames = []
    for name, s in WINDOWS.items():
        t0 = time.time()
        frames.append(build_window(md, name, s))
        f = frames[-1]
        print(f"{name}: {int((f.entry_idx >= 0).sum())} live-hour candles with a zero-spread entry "
              f"({time.time() - t0:.0f}s)", flush=True)
    mt5.shutdown()
    allc = pd.concat(frames)
    allc.to_pickle(Path(__file__).resolve().parent.parent / "backtest_results" / "signal_lab_frames.pkl")

    results = []
    cand = {}
    for f in frames:
        for k, v in rules(f).items():
            cand.setdefault(k, []).append(v)
    if live is not None:
        # The live MACD signal exactly as it traded (recorded live-config runs), plus the same signal
        # kept only when it agrees / disagrees with the 1h and 4h price trend.
        for f in frames:
            lv = live[live.w == f.w.iloc[0]]
            base = pd.Series(0, index=f.index)
            base.loc[base.index.intersection(lv.time)] = np.where(
                lv.set_index("time").direction.reindex(base.index.intersection(lv.time)) == "buy", 1, -1)
            cand.setdefault("LIVE_macd", []).append(base)
            r = rules(f)
            for n in (60, 240):
                tr = r[f"trend_ret{n}"]
                cand.setdefault(f"LIVE_with_trend{n}", []).append(base.where(base == tr, 0))
                cand.setdefault(f"LIVE_against_trend{n}", []).append(base.where(base == -tr, 0))
    for k, parts in cand.items():
        xs = pd.concat([score(f, v) for f, v in zip(frames, parts)])
        if len(xs) == 0:
            continue
        per = xs.groupby("w").apply(lambda q: pd.Series({"n": len(q), "edge": q.win.mean() - q.rand.mean()}),
                                    include_groups=False)
        big = per[per.n >= 100]
        p = xs.win.mean()
        results.append({
            "rule": k, "trades": len(xs), "win%": round(100 * p, 2), "rand%": round(100 * xs.rand.mean(), 2),
            "edge pp": round(100 * (p - xs.rand.mean()), 2),
            "windows +": f"{int((big.edge > 0).sum())}/{len(big)}",
            "est $/tr": round(p * WIN_PAYOFF - (1 - p) * LOSS_PAYOFF, 3),
        })
    t = pd.DataFrame(results).set_index("rule").sort_values("edge pp", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\nbarrier +1 pip / -0.5 pip; random walk 33.33%; break-even with live fills {100 * BREAKEVEN:.1f}%")
    print(t.to_string())


if __name__ == "__main__":
    main()
