"""Usage: PYTHONPATH=. pipenv run python scripts/m5_candle_extrapolation.py  (needs the lab caches from --hours=live/ldn_ny)

Candle-only extrapolation: momentum signals on M5 bars, outcomes played on the following M1 highs/lows.

Entry at the M5 close (bid, no spread); one position at a time; a fixed target/stop race on M1 bars
(if both levels sit inside the same M1 bar it counts as a loss -- conservative). Costs are NOT in the
gross numbers; net = gross - COST pips per trade (assumed, from tick runs: spread reopen + stop slippage).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.argv = [sys.argv[0]]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import signal_lab as L  # noqa: E402

PIP = 1e-4
GEOMS = [(3.0, 3.0), (5.0, 5.0), (5.0, 2.5), (8.0, 4.0)]
COST = 0.20
MAX_BARS = 2880  # 2 days of M1


def m5_from_m1(f: pd.DataFrame) -> pd.DataFrame:
    g = f[["open", "high", "low", "close"]].resample("5min", label="left", closed="left")
    m5 = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()}).dropna()
    flags = f[["in_window", "live_hour"]].resample("5min", label="left", closed="left").first().reindex(m5.index)
    return m5.join(flags)


def race(hi, lo, start, entry, side, tp, sl):
    h, l = hi[start:start + MAX_BARS], lo[start:start + MAX_BARS]
    if side > 0:
        up, dn = h - entry >= tp * PIP - 1e-9, entry - l >= sl * PIP - 1e-9
    else:
        up, dn = entry - l >= tp * PIP - 1e-9, h - entry >= sl * PIP - 1e-9
    iu = np.argmax(up) if up.any() else None
    idn = np.argmax(dn) if dn.any() else None
    if iu is None and idn is None:
        return np.nan, start + len(h)
    if idn is None or (iu is not None and iu < idn):
        return tp, start + iu
    return -sl, start + idn


def run(hours: str) -> None:
    allc = pd.read_pickle(Path(__file__).resolve().parent.parent / "backtest_results" / f"signal_lab_frames_{hours}.pkl")
    rows = []
    for w in L.WINDOWS:
        f = allc[allc.w == w]
        if f.empty:
            continue
        m5 = m5_from_m1(f)
        t, fl = L.momentum_triggers(m5), L.strength_filters(m5)
        r = L.rules(m5)
        sigs = {k: t[k] for k in ("kama_slope", "keltner_break", "roc_break", "hull_slope")}
        sigs.update({f"{k} + volexp": t[k].where(fl["volexp1.3"], 0) for k in ("kama_slope", "keltner_break", "roc_break", "hull_slope")})
        sigs["rsi_fade (contrast)"] = r["rsi_fade"]
        sigs["boll_fade (contrast)"] = r["boll_fade"]
        m1_idx = f.index
        hi, lo = f.high.to_numpy(), f.low.to_numpy()
        # M1 position of the first bar after each M5 bar closes
        nxt = np.searchsorted(m1_idx.values, (m5.index + pd.Timedelta(minutes=5)).values)
        ok = (m5.in_window.fillna(False).astype(bool) & m5.live_hour.fillna(False).astype(bool)).to_numpy()
        for name, s in sigs.items():
            d = s.to_numpy()
            for tp, sl in GEOMS:
                busy_until = -1
                res, rnd = [], []
                for i in np.flatnonzero(ok & (d != 0)):
                    st = nxt[i]
                    if st <= busy_until or st >= len(hi):
                        continue
                    entry = m5.close.iat[i]
                    v, end = race(hi, lo, st, entry, int(d[i]), tp, sl)
                    if np.isnan(v):
                        continue
                    vb, _ = race(hi, lo, st, entry, +1, tp, sl)
                    vs, _ = race(hi, lo, st, entry, -1, tp, sl)
                    busy_until = end
                    res.append(v)
                    rnd.append(np.nanmean([vb, vs]))
                rows.append({"w": w, "signal": name, "exit": f"+{tp:g}/-{sl:g}", "n": len(res),
                             "gross": np.mean(res) if res else np.nan, "rand": np.mean(rnd) if rnd else np.nan,
                             "win": np.mean(np.array(res) > 0) if res else np.nan})
        print(f"{hours} {w} done", flush=True)
    x = pd.DataFrame(rows)
    x["sum"] = x.gross * x.n
    x["rsum"] = x.rand * x.n
    agg = x.groupby(["signal", "exit"]).apply(lambda q: pd.Series({
        "trades": int(q.n.sum()), "trades/wk": round(q.n.sum() / (len(q) * 4), 1),
        "win%": round(100 * (q.win * q.n).sum() / q.n.sum(), 1),
        "gross pip/tr": round(q["sum"].sum() / q.n.sum(), 3),
        "edge vs rand": round((q["sum"].sum() - q.rsum.sum()) / q.n.sum(), 3),
        f"net pip/tr (-{COST})": round(q["sum"].sum() / q.n.sum() - COST, 3),
        "windows net+": f"{int(((q.gross - COST) > 0)[q.n >= 10].sum())}/{int((q.n >= 10).sum())}",
    }), include_groups=False)
    pd.set_option("display.width", 250)
    print(f"\n== M5 candle extrapolation, hours={hours}; one position at a time; entry at M5 close ==")
    print(agg.to_string())


if __name__ == "__main__":
    for h in ("ldn_ny", "live"):
        run(h)
