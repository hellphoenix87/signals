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

# --symbol=<MT5 symbol> (default EURUSD). JPY quotes: pip 0.01, point 0.001.
SYMBOL = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--symbol=")), None) or "EURUSD"
PIP = 0.01 if SYMBOL.endswith("JPY") else 0.0001
UP, DN = 1.0 * PIP, 0.5 * PIP          # staircase first tier ($2) / post-BE cap ($1) at 0.2 lots
WIN_PAYOFF, LOSS_PAYOFF = 2.216, 1.184  # measured average live fills (14 windows)
BREAKEVEN = LOSS_PAYOFF / (WIN_PAYOFF + LOSS_PAYOFF)
POINT = PIP / 10
CHUNKS = (400, 4000, 40000, 200000)     # scan lengths for the first-passage search
# --geometry mode: (target pips, stop pips). Fill slippage measured on live runs: stops fill
# ~0.09 pip past the level (cap -$1.18 vs -$1.00), targets ~0.04 pip short ($2 tier fills +$1.92).
GEOMETRIES = [(1.0, 0.5), (0.5, 0.5), (0.5, 1.0), (0.75, 0.75), (1.0, 1.0)]
SLIP_STOP, SLIP_TARGET = 0.09, 0.04
GEOMETRY_SIGNALS = ["LIVE_macd", "boll_fade", "rsi_fade", "LIVE_against_trend240", "RANDOM"]

WINDOWS = {  # spent windows only -- never screen on the unseen ones reserved for Test P
    "W1": "2026-08-25", "W2": "2026-07-28",
    "W31": "2024-05-07", "W32": "2024-04-09", "W33": "2024-03-12", "W34": "2024-02-13",
    "W35": "2024-01-16", "W36": "2023-12-19", "W37": "2023-11-21", "W38": "2023-10-24",
    "W39": "2023-09-26", "W40": "2023-08-29", "W41": "2023-08-01", "W42": "2023-07-04",
}

JPY_WINDOWS = {f"J{i + 1}": d for i, d in enumerate([
    "2021-04-06", "2021-10-05", "2022-04-05", "2022-10-04", "2023-04-04", "2023-10-03",
    "2024-04-02", "2024-10-01", "2025-04-01", "2025-10-07", "2026-04-07", "2026-08-04"])}
if next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--windows=")), None) == "jpy":
    WINDOWS = JPY_WINDOWS

_SESSION = SessionFilteredSignalStrategy(None, [], lambda c: None,
                                         broker_timezone=getattr(Config, "BROKER_TIMEZONE", "Europe/Athens"))
BLOCKED = set(getattr(Config, "SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL", {}).get(SYMBOL, []))
WAIT_S = float(getattr(Config, "SPREAD_WAIT_SECONDS", 15.0))
WAIT_MAX_PTS = float(getattr(Config, "SPREAD_WAIT_MAX_POINTS", 0.5))
if next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--gate-points=")), None) is not None:  # --gate-points=<points>: spread-wait gate for this run
    WAIT_MAX_PTS = float(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--gate-points=")), None))

# --hours=<set>: which UTC hours get entries. `live` = the hours the live session filter allows
# (19-07 UTC today), `ldn_ny` = 08-18 UTC (the hours it blocks), `all` = every hour.
HOUR_SETS = {
    "live": lambda h: h not in BLOCKED,
    "ldn_ny": lambda h: 8 <= h <= 18,
    "all": lambda h: True,
}
HOURS = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--hours=")), "live")
if HOURS not in HOUR_SETS:
    sys.exit(f"--hours must be one of {sorted(HOUR_SETS)}")
CACHE_MODE = "--cached" in sys.argv   # reuse backtest_results/signal_lab_frames_<hours>.pkl if present
MACD_MODE = "--macd" in sys.argv      # add the production MACD (per candle) as a baseline rule


def _ticks(start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    chunks, t = [], start
    while t < end:
        e = min(t + dt.timedelta(days=1), end)
        k = mt5.copy_ticks_range(SYMBOL, t, e, mt5.COPY_TICKS_ALL)
        if k is not None and len(k):
            chunks.append(pd.DataFrame(k)[["time_msc", "bid", "ask"]])
        t = e
    return pd.concat(chunks).drop_duplicates().sort_values("time_msc").reset_index(drop=True)


def _first_passage(px: np.ndarray, start: int, entry: float, side: int, up_pips: float = 1.0, dn_pips: float = 0.5) -> float:
    """1.0 if price reaches entry+up (in trade direction) before entry-dn, 0.0 if the reverse,
    NaN if neither within the longest chunk."""
    UP, DN = up_pips * PIP, dn_pips * PIP
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


_LAST_TICKS: list = []  # [bid, ask] of the most recent build_window call


def build_window(md: MarketData, name: str, start_s: str, tf: int = mt5.TIMEFRAME_M1, geoms: list = None,
                 hours: str = "live") -> pd.DataFrame:
    """Per candle in the chosen hour set: OHLC history for rules + both-direction barrier outcomes."""
    start = dt.datetime.fromisoformat(start_s)
    end = start + dt.timedelta(days=28)
    candles = pd.DataFrame(md.get_candles_range(SYMBOL, tf, start - dt.timedelta(days=3 if tf == mt5.TIMEFRAME_M1 else 10), end))
    candles = candles.set_index("time").sort_index()
    k = _ticks(start, end + dt.timedelta(days=1))
    tm, bid, ask = k.time_msc.to_numpy(), k.bid.to_numpy(), k.ask.to_numpy()
    _LAST_TICKS[:] = [bid, ask]
    spread_pts = (ask - bid) / POINT

    c = candles.copy()
    c["in_window"] = c.index >= start
    in_hours = HOUR_SETS[hours]
    c["live_hour"] = [in_hours(_SESSION._utc_hour(t.to_pydatetime())) for t in c.index]  # "in chosen hours"
    tf_s = {mt5.TIMEFRAME_M1: 60, mt5.TIMEFRAME_M5: 300, mt5.TIMEFRAME_M15: 900}[tf]
    close_ms = np.array([(t.to_pydatetime() + dt.timedelta(seconds=tf_s)).timestamp() * 1000 for t in c.index], dtype="int64")
    a = np.searchsorted(tm, close_ms, side="left")
    b = np.searchsorted(tm, close_ms + int(WAIT_S * 1000), side="right")
    entry_idx = np.full(len(c), -1)
    for n in np.flatnonzero(c.in_window.to_numpy() & c.live_hour.to_numpy()):
        hit = np.flatnonzero(spread_pts[a[n]:b[n]] <= WAIT_MAX_PTS)
        if len(hit):
            entry_idx[n] = a[n] + hit[0]
    c["entry_idx"] = entry_idx
    ok = entry_idx >= 0
    for (tp, sl) in (geoms or (GEOMETRIES if GEOMETRY_MODE else [(1.0, 0.5)])):
        buy = np.full(len(c), np.nan)
        sell = np.full(len(c), np.nan)
        for n in np.flatnonzero(ok):
            i = entry_idx[n]
            buy[n] = _first_passage(bid, i, ask[i], +1, tp, sl)
            sell[n] = _first_passage(ask, i, bid[i], -1, tp, sl)
        if (tp, sl) == (1.0, 0.5) or "win_buy" not in c:
            c["win_buy"], c["win_sell"] = buy, sell
        c[f"win_buy_{tp}_{sl}"], c[f"win_sell_{tp}_{sl}"] = buy, sell
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


# ---- momentum indicators (m1-momentum-entry plan, Phase 2): standard parameters, fixed ----
# Every value at candle i uses candles <= i only (ewm/rolling/shift are causal; the loops run forward).

def _wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()


def _atr(c: pd.DataFrame, n: int) -> pd.Series:
    pc = c.close.shift(1)
    tr = pd.concat([c.high - c.low, (c.high - pc).abs(), (c.low - pc).abs()], axis=1).max(axis=1)
    return _wilder(tr, n)


def _wma(x: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return x.rolling(n).apply(lambda v: np.dot(v, w) / w.sum(), raw=True)


def _adx(c: pd.DataFrame, n: int = 14) -> tuple:
    up, dn = c.high.diff(), -c.low.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=c.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=c.index)
    atr = _atr(c, n)
    pdi, mdi = 100 * _wilder(pdm, n) / atr, 100 * _wilder(mdm, n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return _wilder(dx.fillna(0), n), pdi, mdi


def _efficiency_ratio(close: pd.Series, n: int = 10) -> pd.Series:
    return (close - close.shift(n)).abs() / close.diff().abs().rolling(n).sum().replace(0, np.nan)


def _kama(close: pd.Series, n: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    er = _efficiency_ratio(close, n).fillna(0).to_numpy()
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    px, out = close.to_numpy(), np.full(len(close), np.nan)
    if len(px) > n:
        out[n] = px[n]
        for i in range(n + 1, len(px)):
            out[i] = out[i - 1] + sc[i] * (px[i] - out[i - 1])
    return pd.Series(out, index=close.index)


def _supertrend_dir(c: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    """+1 / -1 trend state of Supertrend(n, mult)."""
    hl2, atr = ((c.high + c.low) / 2).to_numpy(), _atr(c, n).to_numpy()
    close = c.close.to_numpy()
    ub, lb = hl2 + mult * atr, hl2 - mult * atr
    fub, flb, d = ub.copy(), lb.copy(), np.ones(len(c))
    for i in range(1, len(c)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
        if d[i - 1] < 0 and close[i] > fub[i - 1]:
            d[i] = 1
        elif d[i - 1] > 0 and close[i] < flb[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1]
    d[:n] = 0
    return pd.Series(d, index=c.index)


def _aroon(c: pd.DataFrame, n: int = 25) -> tuple:
    since_hi = c.high.rolling(n + 1).apply(lambda v: n - int(np.argmax(v)), raw=True)
    since_lo = c.low.rolling(n + 1).apply(lambda v: n - int(np.argmin(v)), raw=True)
    return 100 * (n - since_hi) / n, 100 * (n - since_lo) / n


def momentum_triggers(c: pd.DataFrame) -> dict:
    """Phase 3.1 triggers: +1 buy / -1 sell / 0 hold per candle."""
    cl = c.close
    ema, atr10 = cl.ewm(span=20, adjust=False).mean(), _atr(c, 10)
    out = {"keltner_break": np.where(cl > ema + 2 * atr10, 1, np.where(cl < ema - 2 * atr10, -1, 0))}
    st = _supertrend_dir(c)
    out["supertrend_flip"] = np.where((st != st.shift(1)) & (st.shift(1) != 0), st, 0)
    kama = _kama(cl)
    out["kama_slope"] = np.sign(kama - kama.shift(3))
    n = 21
    hull = _wma(2 * _wma(cl, n // 2) - _wma(cl, n), int(np.sqrt(n)))
    out["hull_slope"] = np.sign(hull - hull.shift(1))
    au, ad = _aroon(c)
    out["aroon_cross"] = np.where((au > ad) & (au.shift(1) <= ad.shift(1)), 1,
                                  np.where((au < ad) & (au.shift(1) >= ad.shift(1)), -1, 0))
    roc = cl / cl.shift(10) - 1
    sd = roc.rolling(100).std()
    out["roc_break"] = np.where(roc > sd, 1, np.where(roc < -sd, -1, 0))
    return {k: pd.Series(v, index=c.index).fillna(0).astype(int) for k, v in out.items()}


def strength_filters(c: pd.DataFrame) -> dict:
    """Phase 3.2 strength filters: True where the candle qualifies."""
    adx, _, _ = _adx(c)
    return {
        "adx_rising": ((adx > adx.shift(3)) & (adx >= 20)).fillna(False),
        "er>=0.3": (_efficiency_ratio(c.close, 10) >= 0.3).fillna(False),
        "volexp1.3": (_atr(c, 10) / _atr(c, 100) >= 1.3).fillna(False),
    }


def score(frame: pd.DataFrame, direction: pd.Series) -> pd.DataFrame:
    """Per window: trades, win rate in the rule's direction, same-tick random-direction rate."""
    x = frame.assign(dirn=direction.reindex(frame.index).fillna(0).astype(int))
    x = x[(x.dirn != 0) & x.win_buy.notna() & x.win_sell.notna()]
    x = x.assign(win=np.where(x.dirn > 0, x.win_buy, x.win_sell), rand=(x.win_buy + x.win_sell) / 2)
    return x


GEOMETRY_MODE = "--geometry" in sys.argv
P1_MODE = "--p1-screen" in sys.argv
P1B_MODE = "--p1b-screen" in sys.argv
# Test P1b (pre-registered 2026-09-26): UNSEEN tight-spread windows, used only by --p1b-screen.
P1B_WINDOWS = {"W43": "2023-06-06", "W44": "2023-05-09", "W45": "2023-04-11",
               "W46": "2023-03-14", "W47": "2023-02-14", "W48": "2023-01-17"}
# Test P1 (pre-registered 2026-09-26): UNSEEN windows, used only by --p1-screen.
P1_WINDOWS = {"W25": "2024-10-22", "W26": "2024-09-24", "W27": "2024-08-27",
              "W28": "2024-07-30", "W29": "2024-07-02", "W30": "2024-06-04"}


def p1_screen(md: MarketData, windows: dict = None) -> None:
    """Test P1 exactly as pre-registered: RSI(14) fade on M5, first entry per episode, +/-5 pip."""
    geoms = [(5.0, 5.0), (3.0, 3.0)]
    rows, pooled = [], {g: [] for g in geoms}
    for name, st in (windows or P1_WINDOWS).items():
        f = build_window(md, name, st, mt5.TIMEFRAME_M5, geoms)
        d = rules(f)["rsi_fade"]
        d = d.where(d != d.shift(1).fillna(0), 0)          # first candle of each same-direction run
        for tp, sl in geoms:
            b, s_ = f[f"win_buy_{tp}_{sl}"], f[f"win_sell_{tp}_{sl}"]
            x = f.assign(dirn=d, wb=b, ws=s_)
            valid = (x.dirn != 0) & (x.entry_idx >= 0)
            unres = int((valid & (x.wb.isna() | x.ws.isna())).sum())
            x = x[valid & x.wb.notna() & x.ws.notna()]
            win = pd.Series(np.where(x.dirn > 0, x.wb, x.ws), index=x.index)
            net = win * (tp - SLIP_TARGET) - (1 - win) * (sl + SLIP_STOP)
            rnd = (x.wb + x.ws) / 2
            pooled[(tp, sl)].append(pd.DataFrame({"w": name, "win": win, "rand": rnd, "net": net}))
            rows.append({"window": name, "barrier": f"+/-{tp:g}", "trades": len(x), "unresolved": unres,
                         "win%": round(100 * win.mean(), 1) if len(x) else None,
                         "edge pp": round(100 * (win.mean() - rnd.mean()), 2) if len(x) else None,
                         "net pips/tr": round(net.mean(), 3) if len(x) else None})
        print(f"{name} done", flush=True)
    pd.set_option("display.width", 200)
    print(pd.DataFrame(rows).set_index(["barrier", "window"]).sort_index().to_string())
    for g, parts in pooled.items():
        x = pd.concat(parts)
        per = x.groupby("w").agg(n=("net", "size"), net=("net", "mean"))
        counted = per[per.n >= 20]
        pos = int((counted.net > 0).sum())
        p = x.win.mean()
        verdict = ("INCONCLUSIVE" if len(counted) < 3 else
                   "PASS" if (x.net.mean() > 0 and p - x.rand.mean() > 0 and pos >= 2 * len(counted) / 3) else "FAIL")
        tag = "GRADED" if g == (5.0, 5.0) else "secondary"
        print(f"POOLED +/-{g[0]:g} [{tag}]: trades {len(x)}, win {100 * p:.1f}%, edge {100 * (p - x.rand.mean()):+.2f} pp, "
              f"net {x.net.mean():+.3f} pip/tr (${2 * x.net.mean():+.2f} at 0.2 lot), windows net+ {pos}/{len(counted)} counted"
              + (f" -> {verdict}" if g == (5.0, 5.0) else ""))
SCALE_MODE = "--scale" in sys.argv
SCALE_GEOMS = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (5.0, 5.0)]


def production_macd_directions(c: pd.DataFrame) -> pd.Series:
    """The live signal (production strategy_factory, STF, no session/spread wrappers -- the lab
    applies those itself) evaluated candle by candle on whatever timeframe `c` holds."""
    from app.signals.signal_generation import strategy_factory
    cfg = type("LabCfg", (Config,), {"USE_SESSION_FILTER": False, "USE_SPREAD_WAIT_ENTRY": False,
                                     "USE_MULTI_TIMEFRAME_SIGNALS": False})
    strat = strategy_factory(config=cfg, symbol=SYMBOL, use_multi=False)
    recs = [{"time": t, "open": r.open, "high": r.high, "low": r.low, "close": r.close,
             "tick_volume": getattr(r, "tick_volume", 0), "symbol": SYMBOL} for t, r in c.iterrows()]
    out = np.zeros(len(recs), dtype=int)
    import logging, contextlib, io
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            for i in np.flatnonzero(c.entry_idx.to_numpy() >= 0):
                sig = strat.generate_signal(recs[max(0, i - 400): i + 1])
                fs = (sig or {}).get("final_signal", "hold") if isinstance(sig, dict) else "hold"
                out[i] = 1 if fs == "buy" else (-1 if fs == "sell" else 0)
    finally:
        logging.disable(logging.NOTSET)
    return pd.Series(out, index=c.index)


def scale_table(sets: dict) -> None:
    """sets: label -> (frames, {signal: [per-frame direction series]}, min_trades)"""
    rows = []
    for label, (frames, cand, min_n) in sets.items():
        for k, parts in cand.items():
            for tp, sl in SCALE_GEOMS:
                xs = []
                for f, v in zip(frames, parts):
                    b, s_ = f[f"win_buy_{tp}_{sl}"], f[f"win_sell_{tp}_{sl}"]
                    x = f.assign(dirn=v.reindex(f.index).fillna(0).astype(int), wb=b, ws=s_)
                    x = x[(x.dirn != 0) & x.wb.notna() & x.ws.notna()]
                    win = (x.wb + x.ws) / 2 if k == "RANDOM" else np.where(x.dirn > 0, x.wb, x.ws)
                    xs.append(pd.DataFrame({"w": x.w, "win": win, "rand": (x.wb + x.ws) / 2}))
                xs = pd.concat(xs)
                if len(xs) == 0:
                    continue
                net = xs.win * (tp - SLIP_TARGET) - (1 - xs.win) * (sl + SLIP_STOP)
                per = xs.assign(net=net).groupby("w").agg(n=("net", "size"), net=("net", "mean"))
                big = per[per.n >= min_n]
                p = xs.win.mean()
                rows.append({"tf": label, "signal": k, "barrier": f"+/-{tp:g}", "trades": len(xs),
                             "win%": round(100 * p, 2), "edge pp": round(100 * (p - xs.rand.mean()), 2),
                             "gross pips/tr": round(p * tp - (1 - p) * sl, 4), "net pips/tr": round(net.mean(), 4),
                             "net $/tr": round(2 * net.mean(), 3),
                             "windows net+": f"{int((big.net > 0).sum())}/{len(big)}"})
    t = pd.DataFrame(rows).set_index(["tf", "signal", "barrier"])
    pd.set_option("display.width", 250)
    print(f"\nSCALE SCAN (slippage: stops -{SLIP_STOP} pip, targets -{SLIP_TARGET} pip; unresolved barriers dropped)")
    print(t.to_string())


def geometry_table(frames: list, cand: dict) -> None:
    """Score the chosen signals under each (target, stop) pair: win rate, same-tick random rate,
    gross and net pips per trade, windows with positive net."""
    rows = []
    for k in GEOMETRY_SIGNALS:
        parts = cand.get(k) if k != "RANDOM" else [pd.Series(1, index=f.index) for f in frames]
        if parts is None:
            continue
        for tp, sl in GEOMETRIES:
            xs = []
            for f, v in zip(frames, parts):
                b, s_ = f[f"win_buy_{tp}_{sl}"], f[f"win_sell_{tp}_{sl}"]
                x = f.assign(dirn=v.reindex(f.index).fillna(0).astype(int), wb=b, ws=s_)
                x = x[(x.dirn != 0) & x.wb.notna() & x.ws.notna()]
                win = np.where(x.dirn > 0, x.wb, x.ws) if k != "RANDOM" else (x.wb + x.ws) / 2
                xs.append(pd.DataFrame({"w": x.w, "win": win, "rand": (x.wb + x.ws) / 2}))
            xs = pd.concat(xs)
            net = xs.win * (tp - SLIP_TARGET) - (1 - xs.win) * (sl + SLIP_STOP)
            per = xs.assign(net=net).groupby("w").agg(n=("net", "size"), net=("net", "mean"))
            big = per[per.n >= 100]
            p = xs.win.mean()
            rows.append({"signal": k, "target/stop pips": f"+{tp}/-{sl}", "trades": len(xs),
                         "win%": round(100 * p, 2), "rw break-even%": round(100 * sl / (tp + sl), 2),
                         "edge vs same-tick random pp": round(100 * (p - xs.rand.mean()), 2),
                         "gross pips/tr": round(p * tp - (1 - p) * sl, 4),
                         "net pips/tr": round(net.mean(), 4), "net $/tr (0.2 lot)": round(2 * net.mean(), 3),
                         "windows net+": f"{int((big.net > 0).sum())}/{len(big)}"})
    t = pd.DataFrame(rows).set_index(["signal", "target/stop pips"])
    pd.set_option("display.width", 250)
    print(f"\nGEOMETRY SCAN (slippage: stops -{SLIP_STOP} pip, targets -{SLIP_TARGET} pip)")
    print(t.to_string())


def load_frames(md: MarketData | None) -> list:
    """Per-window frames for HOURS: from the cache when --cached and present, else built from MT5
    (and cached). Returns frames in WINDOWS order."""
    path = Path(__file__).resolve().parent.parent / "backtest_results" / f"signal_lab_frames_{_cache_tag()}{HOURS}.pkl"
    if CACHE_MODE and path.exists():
        allc = pd.read_pickle(path)
        return [allc[allc.w == name] for name in WINDOWS if (allc.w == name).any()]
    frames = []
    for name, s in WINDOWS.items():
        t0 = time.time()
        frames.append(build_window(md, name, s, hours=HOURS))
        f = frames[-1]
        print(f"{name}: {int((f.entry_idx >= 0).sum())} {HOURS}-hour candles with a zero-spread entry "
              f"({time.time() - t0:.0f}s)", flush=True)
    pd.concat(frames).to_pickle(path)
    return frames


def _cache_tag() -> str:
    return "" if SYMBOL == "EURUSD" else f"{SYMBOL}_g{WAIT_MAX_PTS:g}_"


HYPOTHESES_MODE = "--hypotheses" in sys.argv
HYP_MIN_TRADES = 50


def _hyp_row(label: str, frames: list, parts: list, tp: float, sl: float, rnd: bool = False) -> dict:
    """Net pips/trade (after slippage) of a direction series at one fixed exit, per window and pooled."""
    per, allx = [], []
    for f, d in zip(frames, parts):
        b, s_ = f[f"win_buy_{tp}_{sl}"], f[f"win_sell_{tp}_{sl}"]
        q = f.assign(d=d.reindex(f.index).fillna(0).astype(int), wb=b, ws=s_)
        valid = (q.d != 0) & (q.entry_idx >= 0)
        unres = int((valid & (q.wb.isna() | q.ws.isna())).sum())
        q = q[valid & q.wb.notna() & q.ws.notna()]
        rw = (q.wb + q.ws) / 2
        w = rw if rnd else pd.Series(np.where(q.d > 0, q.wb, q.ws), index=q.index)
        net = w * (tp - SLIP_TARGET) - (1 - w) * (sl + SLIP_STOP)
        rnet = rw * (tp - SLIP_TARGET) - (1 - rw) * (sl + SLIP_STOP)
        allx.append(pd.DataFrame({"net": net, "rnet": rnet, "win": w}))
        per.append({"hyp": label, "window": f.w.iloc[0], "trades": len(q), "unresolved": unres,
                    "win%": round(100 * w.mean(), 1) if len(q) else None,
                    "net pip/tr": round(net.mean(), 3) if len(q) else None})
    x = pd.concat(allx)
    pw = pd.DataFrame(per)
    counted = pw[pw.trades >= HYP_MIN_TRADES]
    pos = int((counted["net pip/tr"] > 0).sum())
    net, edge = x.net.mean(), x.net.mean() - x.rnet.mean()
    se = x.net.std() / np.sqrt(len(x)) if len(x) > 1 else np.nan
    verdict = "" if rnd else ("PASS" if (net > 0 and edge > 0 and len(counted) and pos >= 2 / 3 * len(counted)) else "FAIL")
    return {"per": pw, "pooled": {"hyp": label, "trades": len(x), "win%": round(100 * x.win.mean(), 2),
                                  "net pip/tr": round(net, 4), "(naive se)": round(se, 4),
                                  "edge vs random": round(edge, 4), "$/tr @0.2lot": None,
                                  "windows net+": f"{pos}/{len(counted)}", "verdict": verdict}}


def hypotheses(md: MarketData) -> None:
    """The pre-registered USDJPY hypotheses H1-H3 (docs/plans/in-progress/usdjpy-test.md), judged once."""
    def frames_for(tf: int, geom: tuple) -> list:
        name = {mt5.TIMEFRAME_M1: "M1", mt5.TIMEFRAME_M5: "M5"}[tf]
        path = Path(__file__).resolve().parent.parent / "backtest_results" / f"signal_lab_hyp_{_cache_tag()}{name}_{HOURS}.pkl"
        if CACHE_MODE and path.exists():
            allc = pd.read_pickle(path)
            return [allc[allc.w == n] for n in WINDOWS if (allc.w == n).any()]
        out = []
        for n, st in WINDOWS.items():
            t0 = time.time()
            out.append(build_window(md, n, st, tf, [geom], hours=HOURS))
            print(f"{name} {n}: {int((out[-1].entry_idx >= 0).sum())} entries ({time.time() - t0:.0f}s)", flush=True)
        pd.concat(out).to_pickle(path)
        return out

    m1 = frames_for(mt5.TIMEFRAME_M1, (3.0, 3.0))
    m5 = frames_for(mt5.TIMEFRAME_M5, (5.0, 5.0))
    mt5.shutdown()

    def kama_vx(frames: list) -> list:
        return [momentum_triggers(f)["kama_slope"].where(strength_filters(f)["volexp1.3"], 0) for f in frames]

    rows = [
        _hyp_row("H1 MACD_prod M1 +3/-3", m1, [production_macd_directions(f) for f in m1], 3.0, 3.0),
        _hyp_row("   random, same M1 ticks", m1, [pd.Series(1, index=f.index) for f in m1], 3.0, 3.0, rnd=True),
        _hyp_row("H2 KAMA+volexp M1 +3/-3", m1, kama_vx(m1), 3.0, 3.0),
        _hyp_row("H3 KAMA+volexp M5 +5/-5", m5, kama_vx(m5), 5.0, 5.0),
        _hyp_row("   random, same M5 ticks", m5, [pd.Series(1, index=f.index) for f in m5], 5.0, 5.0, rnd=True),
    ]
    pd.set_option("display.width", 250)
    print(f"\n{SYMBOL} hours={HOURS} gate<={WAIT_MAX_PTS:g} points; slippage stops {SLIP_STOP} / targets {SLIP_TARGET} pip")
    print(pd.concat([r["per"] for r in rows]).set_index(["hyp", "window"]).to_string())
    pooled = pd.DataFrame([r["pooled"] for r in rows]).drop(columns=["$/tr @0.2lot"]).set_index("hyp")
    print("\nPOOLED (bar: net > 0, edge > 0, net+ in >= 2/3 of windows with >= 50 trades)")
    print(pooled.to_string())


MOMENTUM_MODE = "--momentum" in sys.argv
# Pass bar, fixed in the plan before any screen.
BAR_EDGE_PP, BAR_WINDOW_SHARE, BAR_MIN_TRADES = 3.0, 10 / 12, 100


def _summarise(frames: list, parts: list) -> dict:
    xs = pd.concat([score(f, v) for f, v in zip(frames, parts)])
    if len(xs) == 0:
        return {"trades": 0}
    per = xs.groupby("w").agg(n=("win", "size"), win=("win", "mean"), rand=("rand", "mean"))
    big = per[per.n >= BAR_MIN_TRADES]
    pos = int((big.win > big.rand).sum())
    p = xs.win.mean()
    return {"trades": len(xs), "trades/win": round(len(xs) / len(frames)), "win%": round(100 * p, 2),
            "rand%": round(100 * xs.rand.mean(), 2), "edge pp": round(100 * (p - xs.rand.mean()), 2),
            "windows +": f"{pos}/{len(big)}", "_pos": pos, "_n": len(big)}


def momentum_screen(frames: list) -> None:
    """Phase 3.1 (triggers alone) and 3.2 (trigger + one strength filter) for the current HOURS,
    every configuration reported. `vs prev` = edge minus the simpler layer's edge (trigger alone
    for 3.2). PASS = pooled edge >= 3 pp, positive in >= 10/12 of windows with >= 100 trades,
    above the MACD baseline (when --macd) and above the previous layer."""
    trig = [momentum_triggers(f) for f in frames]
    filt = [strength_filters(f) for f in frames]
    rows = []
    macd_edge = None
    if MACD_MODE:
        r = _summarise(frames, [production_macd_directions(f) for f in frames])
        macd_edge = r.get("edge pp")
        rows.append({"layer": "base", "config": "MACD_prod", **r})
    rows.append({"layer": "base", "config": "RANDOM", **_summarise_random(frames)})
    for k in trig[0]:
        r1 = _summarise(frames, [t[k] for t in trig])
        rows.append({"layer": "3.1", "config": k, **r1, "vs prev": None})
        for fk in filt[0]:
            r2 = _summarise(frames, [t[k].where(fl[fk], 0) for t, fl in zip(trig, filt)])
            prev = r1.get("edge pp")
            rows.append({"layer": "3.2", "config": f"{k} + {fk}", **r2,
                         "vs prev": None if prev is None or "edge pp" not in r2 else round(r2["edge pp"] - prev, 2)})
    for r in rows:
        if r["layer"] == "base" or not r.get("trades"):
            r["PASS"] = ""
            continue
        ok = (r["edge pp"] >= BAR_EDGE_PP and r["_n"] > 0 and r["_pos"] >= BAR_WINDOW_SHARE * r["_n"]
              and (macd_edge is None or r["edge pp"] > macd_edge)
              and (r["layer"] == "3.1" or (r["vs prev"] or 0) > 0))
        r["PASS"] = "PASS" if ok else ""
    t = pd.DataFrame(rows).drop(columns=["_pos", "_n"], errors="ignore").set_index(["layer", "config"])
    pd.set_option("display.width", 250)
    print(f"\nMOMENTUM SCREEN hours={HOURS}; barrier +1/-0.5 pip; break-even with live fills {100 * BREAKEVEN:.1f}%")
    print(t.to_string())


def _summarise_random(frames: list) -> dict:
    xs = pd.concat([score(f, pd.Series(1, index=f.index)) for f in frames])
    return {"trades": len(xs), "trades/win": round(len(xs) / len(frames)), "win%": round(100 * xs.rand.mean(), 2),
            "rand%": round(100 * xs.rand.mean(), 2), "edge pp": 0.0, "windows +": "-"}


# ---- exit grid (momentum-exit-grid plan): wider fixed exits and trailing exits, in pips ----
EXIT_FIXED = [(t, s) for t in (1.0, 2.0, 3.0, 5.0) for s in (0.5, 1.0, 2.0, 3.0)]
EXIT_TRAILS = [(1, 1, 1), (2, 2, 2), (3, 3, 3), (2, 1, 1), (3, 2, 2), (2, 2, 1), (5, 3, 3)]  # (stop, arm, dist)
EXIT_KEYS = [f"fx+{t:g}/-{s:g}" for t, s in EXIT_FIXED] + [f"tr S{s}/A{a}/D{d}" for s, a, d in EXIT_TRAILS]
TUNE_WINDOWS = ("W1", "W2", "W31", "W32", "W33", "W34", "W35")
EXIT_GRID_MODE = "--exit-grid" in sys.argv


def _exit_values(px: np.ndarray, start: int, entry: float, side: int) -> np.ndarray:
    """Net pips for every exit in EXIT_KEYS from one entry (NaN = unresolved within CHUNKS).
    Fixed: +target (less target slippage) or -stop (plus stop slippage), whichever comes first.
    Trail: stop at -S until the peak reaches +A, then at max(-S, peak - D); exits fill at the
    stop level less stop slippage."""
    nf = len(EXIT_FIXED)
    out = np.full(len(EXIT_KEYS), np.nan)
    todo = list(range(len(EXIT_KEYS)))
    for n in CHUNKS:
        seg = (px[start:start + n] - entry) * side / PIP
        peak = None
        rest = []
        for j in todo:
            if j < nf:
                tp, sl = EXIT_FIXED[j]
                up = np.flatnonzero(seg >= tp - 1e-6)
                dn = np.flatnonzero(seg <= -sl + 1e-6)
                if len(up) or len(dn):
                    win = len(up) > 0 and (len(dn) == 0 or up[0] < dn[0])
                    out[j] = (tp - SLIP_TARGET) if win else -(sl + SLIP_STOP)
                    continue
            else:
                if peak is None:
                    peak = np.maximum.accumulate(seg)
                st, arm, dist = EXIT_TRAILS[j - nf]
                lvl = np.where(peak >= arm - 1e-6, np.maximum(-st, peak - dist), -st)
                hit = np.flatnonzero(seg <= lvl + 1e-6)
                if len(hit):
                    out[j] = lvl[hit[0]] - SLIP_STOP
                    continue
            rest.append(j)
        todo = rest
        if not todo or start + n >= len(px):
            break
    return out


def _window_exits(bid: np.ndarray, ask: np.ndarray, entry_idx: np.ndarray, index: pd.Index) -> pd.DataFrame:
    """Both-direction exit values per candle (buy at ask judged on bid, sell at bid judged on ask)."""
    b = np.full((len(entry_idx), len(EXIT_KEYS)), np.nan)
    s_ = np.full((len(entry_idx), len(EXIT_KEYS)), np.nan)
    for n in np.flatnonzero(entry_idx >= 0):
        i = entry_idx[n]
        b[n] = _exit_values(bid, i, ask[i], +1)
        s_[n] = _exit_values(ask, i, bid[i], -1)
    cols = {}
    for j, k in enumerate(EXIT_KEYS):
        cols[f"xb {k}"], cols[f"xs {k}"] = b[:, j], s_[:, j]
    return pd.DataFrame(cols, index=index)


def _exit_stats(frames: list, parts: list, key: str, windows: tuple, rnd: bool) -> dict:
    xs = []
    for f, d in zip(frames, parts):
        if f.w.iloc[0] not in windows:
            continue
        q = f.assign(d=d.reindex(f.index).fillna(0).astype(int))
        q = q[(q.d != 0) & q[f"xb {key}"].notna() & q[f"xs {key}"].notna()]
        r = (q[f"xb {key}"] + q[f"xs {key}"]) / 2
        v = r if rnd else pd.Series(np.where(q.d > 0, q[f"xb {key}"], q[f"xs {key}"]), index=q.index)
        xs.append(pd.DataFrame({"w": q.w, "v": v, "r": r}))
    xs = pd.concat(xs) if xs else pd.DataFrame({"w": [], "v": [], "r": []})
    per = xs.groupby("w").agg(n=("v", "size"), v=("v", "mean"))
    big = per[per.n >= BAR_MIN_TRADES]
    return {"n": len(xs), "net": xs.v.mean() if len(xs) else np.nan,
            "edge": (xs.v.mean() - xs.r.mean()) if len(xs) else np.nan,
            "pos": int((big.v > 0).sum()), "cnt": len(big)}


def exit_grid(md: MarketData) -> None:
    """Momentum candidates x fixed/trailing exits; exits tuned on TUNE_WINDOWS, reported on the rest."""
    path = Path(__file__).resolve().parent.parent / "backtest_results" / f"signal_lab_exitgrid_{_cache_tag()}{HOURS}.pkl"
    if CACHE_MODE and path.exists():
        allc = pd.read_pickle(path)
        frames = [allc[allc.w == name] for name in WINDOWS if (allc.w == name).any()]
    else:
        frames = []
        for name, st in WINDOWS.items():
            t0 = time.time()
            f = build_window(md, name, st, geoms=[(1.0, 0.5)], hours=HOURS)
            bid, ask = _LAST_TICKS
            frames.append(pd.concat([f, _window_exits(bid, ask, f.entry_idx.to_numpy(), f.index)], axis=1))
            print(f"{name}: {int((f.entry_idx >= 0).sum())} entries ({time.time() - t0:.0f}s)", flush=True)
        pd.concat(frames).to_pickle(path)
    mt5.shutdown()

    x = pd.concat(frames)
    ok = x["win_buy"].notna() & x["xb fx+1/-0.5"].notna()
    agree = ((x.loc[ok, "xb fx+1/-0.5"] > 0) == (x.loc[ok, "win_buy"] > 0.5)).mean()
    print(f"fixed +1/-0.5 agreement with the barrier outcome: {100 * agree:.2f}% of {int(ok.sum())} buys")
    for key in EXIT_KEYS:
        un = int((x.entry_idx >= 0).sum() - x[f"xb {key}"].notna().sum())
        if un:
            print(f"  unresolved buys for {key}: {un}")

    cands = {}
    for f in frames:
        t, fl = momentum_triggers(f), strength_filters(f)
        vx = fl["volexp1.3"]
        for k in ("kama_slope", "keltner_break", "roc_break", "hull_slope"):
            cands.setdefault(f"{k} + volexp", []).append(t[k].where(vx, 0))
        cands.setdefault("kama_slope", []).append(t["kama_slope"])
        cands.setdefault("keltner_break", []).append(t["keltner_break"])
        cands.setdefault("RANDOM", []).append(pd.Series(1, index=f.index))
        if MACD_MODE:
            cands.setdefault("MACD_prod", []).append(production_macd_directions(f))

    hold = tuple(w for w in WINDOWS if w not in TUNE_WINDOWS)
    pd.set_option("display.width", 250)
    summary = []
    for name, parts in cands.items():
        rows = []
        for key in EXIT_KEYS:
            tu = _exit_stats(frames, parts, key, TUNE_WINDOWS, name == "RANDOM")
            ho = _exit_stats(frames, parts, key, hold, name == "RANDOM")
            rows.append({"exit": key, "tune n": tu["n"], "tune net": round(tu["net"], 4),
                         "tune edge": round(tu["edge"], 4), "tune w+": f"{tu['pos']}/{tu['cnt']}",
                         "hold n": ho["n"], "hold net": round(ho["net"], 4), "hold edge": round(ho["edge"], 4),
                         "hold w+": f"{ho['pos']}/{ho['cnt']}", "_tn": tu["net"], "_ho": ho})
        t = pd.DataFrame(rows)
        print(f"\n== {name} (hours={HOURS}; net pips/trade after slippage; edge = vs same-tick random) ==")
        print(t.drop(columns=["_tn", "_ho"]).set_index("exit").to_string())
        best = t.loc[t._tn.idxmax()]
        ho = best._ho
        rnd_ho = _exit_stats(frames, cands["RANDOM"], best.exit, hold, True)["net"]
        passed = (name != "RANDOM" and ho["net"] > 0 and ho["cnt"] > 0 and ho["pos"] >= 5 / 7 * ho["cnt"]
                  and ho["net"] > rnd_ho)
        summary.append({"candidate": name, "tuned exit": best.exit, "tune net": round(best._tn, 4),
                        "hold net": round(ho["net"], 4), "hold $/tr": round(2 * ho["net"], 3),
                        "hold edge": round(ho["edge"], 4), "hold w+": f"{ho['pos']}/{ho['cnt']}",
                        "hold RANDOM net": round(rnd_ho, 4), "SMOKE": "pass" if passed else ""})
    print(f"\n== SUMMARY hours={HOURS}: exit tuned on {', '.join(TUNE_WINDOWS)}; reported on {', '.join(hold)} ==")
    print(pd.DataFrame(summary).set_index("candidate").to_string())


def main() -> None:
    for _ in range(10):
        if mt5.initialize():
            break
        time.sleep(3)
    else:
        sys.exit("MT5 initialization failed")
    md = MarketData()
    if P1_MODE or P1B_MODE:
        p1_screen(md, P1B_WINDOWS if P1B_MODE else P1_WINDOWS)
        mt5.shutdown()
        return
    if EXIT_GRID_MODE:
        exit_grid(md)
        return
    if HYPOTHESES_MODE:
        hypotheses(md)
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if SCALE_MODE:
        live = pd.read_pickle(Path(args[0])) if args else None
        m1, m5 = [], []
        for name, st in WINDOWS.items():
            t0 = time.time()
            m1.append(build_window(md, name, st, mt5.TIMEFRAME_M1, SCALE_GEOMS))
            m5.append(build_window(md, name, st, mt5.TIMEFRAME_M5, SCALE_GEOMS))
            print(f"{name}: M1 {int((m1[-1].entry_idx >= 0).sum())} / M5 {int((m5[-1].entry_idx >= 0).sum())} entries ({time.time() - t0:.0f}s)", flush=True)
        c1, c5 = {}, {}
        for f in m1:
            r = rules(f)
            for k in ("boll_fade", "rsi_fade"):
                c1.setdefault(k, []).append(r[k])
            c1.setdefault("RANDOM", []).append(pd.Series(1, index=f.index))
            if live is not None:
                lv = live[live.w == f.w.iloc[0]]
                base = pd.Series(0, index=f.index)
                idx = base.index.intersection(lv.time)
                base.loc[idx] = np.where(lv.set_index("time").direction.reindex(idx) == "buy", 1, -1)
                c1.setdefault("LIVE_macd", []).append(base)
                c1.setdefault("LIVE_against_trend240", []).append(base.where(base == -r["trend_ret240"], 0))
        for f in m5:
            r = rules(f)
            for k in ("boll_fade", "rsi_fade", "fade_last1"):
                c5.setdefault(k, []).append(r[k])
            c5.setdefault("RANDOM", []).append(pd.Series(1, index=f.index))
            c5.setdefault("MACD_prod_on_M5", []).append(production_macd_directions(f))
        mt5.shutdown()
        scale_table({"M1": (m1, c1, 100), "M5": (m5, c5, 50)})
        return
    live = pd.read_pickle(Path(args[0])) if args else None  # optional live-signal (w, time, direction)
    frames = load_frames(md)
    mt5.shutdown()
    if MOMENTUM_MODE:
        momentum_screen(frames)
        return

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
    if MACD_MODE:
        for f in frames:
            cand.setdefault("MACD_prod", []).append(production_macd_directions(f))
    cand["RANDOM"] = [pd.Series(1, index=f.index) for f in frames]
    for k, parts in cand.items():
        xs = pd.concat([score(f, v) for f, v in zip(frames, parts)])
        if len(xs) == 0:
            continue
        if k == "RANDOM":  # every candle, direction averaged: the hour set's own baseline
            xs = xs.assign(win=xs.rand)
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
    if GEOMETRY_MODE:
        geometry_table(frames, cand)
        return
    t = pd.DataFrame(results).set_index("rule").sort_values("edge pp", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\nbarrier +1 pip / -0.5 pip; random walk 33.33%; break-even with live fills {100 * BREAKEVEN:.1f}%")
    print(t.to_string())


if __name__ == "__main__":
    main()
