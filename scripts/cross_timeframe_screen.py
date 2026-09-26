"""Cross-timeframe smoke screen (docs/plans/in-progress/cross-timeframe-screen.md).

Candle-based, not production code: 4 rules x 5 timeframes (M5-H1) x 6 majors on 2015-2020 M1 data,
ATR-sized exits played forward on M1 bars, a flat 0.5-pip cost, $10 per R. Reports profit pace ($/week)
against drawdown (weeks of profit) and the pre-registered eligibility verdict.

Usage:
    python scripts/cross_timeframe_screen.py            # fetches + caches M1 on first run
    python scripts/cross_timeframe_screen.py --spot     # print a few trades for a hand check
"""

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD"]
TFS = {"M5": 5, "M10": 10, "M15": 15, "M30": 30, "H1": 60}
START, END = dt.datetime(2015, 1, 1), dt.datetime(2021, 1, 1)
COST_PIPS, R_DOLLARS, STOP_ATR, TRAIL_ATR = 0.5, 10.0, 1.5, 1.5
HOLD = {"rsi_fade": 24, "boll_fade": 24, "donchian_break": 48, "kama_volexp": 48}
MOMENTUM = {"donchian_break", "kama_volexp"}
WARMUP_BARS = 100  # ATR(100) / Wilder smoothing settle first
CACHE = Path(__file__).resolve().parent.parent / "backtest_results"


def pip_size(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


def load_m1(pair: str) -> pd.DataFrame:
    path = CACHE / f"xtf_m1_{pair}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    import MetaTrader5 as mt5
    assert mt5.initialize(), mt5.last_error()
    mt5.symbol_select(pair, True)
    parts = []
    for y in range(START.year, END.year):
        for half in ((1, 7), (7, 13)):
            a = dt.datetime(y, half[0], 1)
            b = dt.datetime(y + (half[1] == 13), 1 if half[1] == 13 else 7, 1)
            r = mt5.copy_rates_range(pair, mt5.TIMEFRAME_M1, a, b)
            if r is not None and len(r):
                parts.append(pd.DataFrame(r)[["time", "open", "high", "low", "close"]])
    mt5.shutdown()
    d = pd.concat(parts).drop_duplicates("time").sort_values("time")
    d.index = pd.to_datetime(d.pop("time"), unit="s")
    d.to_pickle(path)
    return d


# ---- indicators (causal: value at bar i uses bars <= i) ----

def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()


def atr(b: pd.DataFrame, n: int) -> pd.Series:
    pc = b.close.shift(1)
    tr = pd.concat([b.high - b.low, (b.high - pc).abs(), (b.low - pc).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up, dn = wilder(d.clip(lower=0), n), wilder((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def kama(close: pd.Series, n: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    er = ((close - close.shift(n)).abs() / close.diff().abs().rolling(n).sum().replace(0, np.nan)).fillna(0).to_numpy()
    sc = (er * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    px, out = close.to_numpy(), np.full(len(close), np.nan)
    if len(px) > n:
        out[n] = px[n]
        for i in range(n + 1, len(px)):
            out[i] = out[i - 1] + sc[i] * (px[i] - out[i - 1])
    return pd.Series(out, index=close.index)


def signals(b: pd.DataFrame) -> dict:
    cl = b.close
    r = rsi(cl)
    m, s = cl.rolling(20).mean(), cl.rolling(20).std()
    hh, ll = b.high.shift(1).rolling(20).max(), b.low.shift(1).rolling(20).min()
    k = kama(cl)
    vx = atr(b, 10) / atr(b, 100) >= 1.3
    raw = {
        "rsi_fade": np.where(r < 30, 1, np.where(r > 70, -1, 0)),
        "boll_fade": np.where(cl < m - 2 * s, 1, np.where(cl > m + 2 * s, -1, 0)),
        "donchian_break": np.where(cl > hh, 1, np.where(cl < ll, -1, 0)),
        "kama_volexp": np.where(vx, np.sign(k - k.shift(3)).fillna(0), 0),
    }
    out = {}
    for name, v in raw.items():
        v = pd.Series(v, index=b.index).fillna(0).astype(int)
        out[name] = v.where(v != v.shift(1).fillna(0), 0)  # first bar of each episode
    return out


# ---- trade simulation on the M1 path ----

def simulate(pair: str, tf: str, m1: pd.DataFrame, spot: bool = False) -> pd.DataFrame:
    mins = TFS[tf]
    b = m1.resample(f"{mins}min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    a14 = atr(b, 14).to_numpy()
    sig = signals(b)
    t1 = m1.index.values
    o, h, l, c = (m1[k].to_numpy() for k in ("open", "high", "low", "close"))
    # first M1 bar after each TF bar closes
    start_idx = np.searchsorted(t1, (b.index + pd.Timedelta(minutes=mins)).values, side="left")
    pip = pip_size(pair)
    rows = []
    for rule, s in sig.items():
        d = s.to_numpy()
        busy = -1
        for j in np.flatnonzero(d != 0):
            p0 = start_idx[j]
            if j < WARMUP_BARS or p0 <= busy or p0 >= len(t1) or not np.isfinite(a14[j]) or a14[j] <= 0:
                continue
            k_end = j + HOLD[rule]
            if k_end >= len(start_idx):
                break
            p1 = start_idx[k_end]  # path = M1 bars p0 .. p1-1 (HOLD TF bars, weekends skipped)
            if p1 <= p0:
                continue
            side, entry = int(d[j]), float(b.close.iat[j])
            risk = STOP_ATR * a14[j]
            # side-normalised path: favourable moves are positive
            if side > 0:
                po, ph, pl, pc = o[p0:p1] - entry, h[p0:p1] - entry, l[p0:p1] - entry, c[p0:p1] - entry
            else:
                po, ph, pl, pc = entry - o[p0:p1], entry - l[p0:p1], entry - h[p0:p1], entry - c[p0:p1]
            if rule in MOMENTUM:
                peak_prev = np.maximum.accumulate(np.concatenate(([0.0], ph[:-1])))
                lvl = np.maximum(-risk, peak_prev - TRAIL_ATR * a14[j])
                hit = np.flatnonzero(pl <= lvl)
                if len(hit):
                    i = hit[0]
                    move, exit_i = min(po[i], lvl[i]), i
                else:
                    move, exit_i = pc[-1], len(pc) - 1
            else:
                sh = np.flatnonzero(pl <= -risk)
                th = np.flatnonzero(ph >= risk)
                si = sh[0] if len(sh) else None
                ti = th[0] if len(th) else None
                if si is not None and (ti is None or si <= ti):
                    move, exit_i = min(po[si], -risk), si
                elif ti is not None:
                    move, exit_i = max(po[ti], risk), ti
                else:
                    move, exit_i = pc[-1], len(pc) - 1
            net_r = move / risk - COST_PIPS * pip / risk
            busy = p0 + exit_i
            rows.append({"pair": pair, "tf": tf, "rule": rule, "side": side,
                         "entry_time": b.index[j] + pd.Timedelta(minutes=mins),
                         "exit_time": m1.index[p0 + exit_i], "entry": entry,
                         "risk_pips": risk / pip, "move_pips": move / pip, "net_r": net_r})
    x = pd.DataFrame(rows)
    if spot and len(x):
        print(x.groupby("rule").head(2).to_string())
    return x


def report(t: pd.DataFrame) -> None:
    weeks = (END - START).days / 7
    out = []
    for (rule, tf), g in t.groupby(["rule", "tf"]):
        g = g.sort_values("exit_time")
        eq = (g.net_r * R_DOLLARS).cumsum()
        dd = float((eq.cummax() - eq).max())
        per_week = eq.iloc[-1] / weeks
        by_pair = g.groupby("pair").net_r.sum()
        by_year = g.groupby(g.exit_time.dt.year).net_r.sum()
        r = g.net_r.mean()
        dd_weeks = dd / per_week if per_week > 0 else np.inf
        eligible = (r >= 0.05 and (by_pair > 0).sum() >= 4 and (by_year > 0).sum() >= 4 and dd_weeks <= 26)
        out.append({"rule": rule, "tf": tf, "trades": len(g),
                    "trades/wk/pair": round(len(g) / weeks / len(PAIRS), 1),
                    "net R/tr": round(r, 3), "win%": round(100 * (g.net_r > 0).mean(), 1),
                    "$/week (6 pairs)": round(per_week, 2), "max DD $": round(dd, 0),
                    "DD weeks": round(dd_weeks, 1) if np.isfinite(dd_weeks) else "inf",
                    "pairs +": f"{int((by_pair > 0).sum())}/{len(by_pair)}",
                    "years +": f"{int((by_year > 0).sum())}/{len(by_year)}",
                    "ELIGIBLE": "YES" if eligible else ""})
    order = {k: i for i, k in enumerate(TFS)}
    res = pd.DataFrame(out).sort_values(["rule", "tf"], key=lambda s: s.map(order) if s.name == "tf" else s)
    pd.set_option("display.width", 250)
    print(f"\nCROSS-TIMEFRAME SCREEN {START.date()}..{END.date()}, {len(PAIRS)} pairs, cost {COST_PIPS} pip, ${R_DOLLARS:g}/R")
    print(res.set_index(["rule", "tf"]).to_string())
    print("\nnet R/trade by pair:")
    print(t.pivot_table(index=["rule", "tf"], columns="pair", values="net_r", aggfunc="mean").round(3).to_string())
    print("\nnet R (sum) by year:")
    print(t.pivot_table(index=["rule", "tf"], columns=t.exit_time.dt.year, values="net_r", aggfunc="sum").round(1).to_string())


def main() -> None:
    spot = "--spot" in sys.argv
    frames = []
    for pair in PAIRS:
        m1 = load_m1(pair)
        m1 = m1[(m1.index >= START) & (m1.index < END)]
        print(f"{pair}: {len(m1)} M1 bars {m1.index[0]} .. {m1.index[-1]}", flush=True)
        for tf in (["M15"] if spot else TFS):
            frames.append(simulate(pair, tf, m1, spot))
        if spot:
            return
    t = pd.concat(frames)
    t.to_pickle(CACHE / "xtf_trades.pkl")
    report(t)


if __name__ == "__main__":
    main()
