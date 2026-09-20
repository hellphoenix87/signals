"""One-off analysis for Thread 1 of `docs/exit-strategy-open-threads.md`:
does EURUSD's session-filter blocked-hours window (08:00-18:59 UTC,
`Config.SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL["EURUSD"]`) transfer to
the other 6 majors, or does each pair need its own?

Reads the per-signal CSVs produced by `scripts/backtest_signals.py --mtf`
(no `--session-filter`, so every UTC hour's raw win rate can be read
before any block is chosen), recovers each signal's true UTC hour from its
candle-frame `time` column using the same offset formula production uses
(`app.signals.signal_generation.get_broker_utc_offset_hours`), and prints,
per pair (pooled across all CSVs passed for that pair):
  - the full hour-by-hour win-rate table (for eyeballing a contiguous
    block, the same judgment call the original EURUSD analysis made --
    see `docs/test-results/session-filter-analysis.md` Finding 4)
  - win rate inside EURUSD's existing 08-18 UTC block vs. outside it

Usage:
    PYTHONPATH=. pipenv run python scripts/analyze_session_filter_by_pair.py \
        backtest_results/EURUSD_mtf_*.csv backtest_results/GBPUSD_mtf_*.csv ...

Each CSV's pair is recovered from its own filename (`<SYMBOL>_mtf_<timestamp>.csv`,
exactly what `backtest_signals.py` writes), so files for the same pair
across multiple windows can just all be passed together and get pooled.
"""

import argparse
import csv
import datetime
import sys
from collections import defaultdict
from pathlib import Path

from app.config.settings import Config
from app.signals.signal_generation import get_broker_utc_offset_hours

EURUSD_BLOCKED_HOURS = set(
    getattr(Config, "SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL", {}).get("EURUSD", range(8, 19))
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_files", nargs="+", help="Per-signal CSVs from backtest_signals.py --mtf")
    return parser.parse_args()


def symbol_from_filename(path: Path) -> str:
    return path.name.split("_mtf_")[0]


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main() -> None:
    args = parse_args()
    offset = get_broker_utc_offset_hours()
    print(f"Broker UTC offset (hours): {offset}\n")

    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for raw_path in args.csv_files:
        path = Path(raw_path)
        symbol = symbol_from_filename(path)
        by_symbol[symbol].extend(load_rows(path))

    summary_rows = []

    for symbol in sorted(by_symbol):
        rows = by_symbol[symbol]
        decided = [r for r in rows if r["outcome"] in ("win", "loss")]
        by_hour: dict[int, list[dict]] = defaultdict(list)
        hour_of: dict[int, int] = {}
        for idx, r in enumerate(decided):
            dt = datetime.datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S")
            utc_hour = (dt.hour - offset) % 24
            by_hour[utc_hour].append(r)
            hour_of[idx] = utc_hour

        print(f"=== {symbol} ({len(decided)} decided signals) ===")
        print(f"{'hour':>4} {'n':>5} {'win%':>6}")
        for hour in range(24):
            hour_rows = by_hour.get(hour, [])
            n = len(hour_rows)
            wins = sum(1 for r in hour_rows if r["outcome"] == "win")
            win_pct = (wins / n * 100.0) if n else float("nan")
            marker = "*" if hour in EURUSD_BLOCKED_HOURS else " "
            print(f"{hour:>4}{marker} {n:>5} {win_pct:>6.1f}" if n else f"{hour:>4}{marker} {n:>5} {'--':>6}")

        blocked = [r for idx, r in enumerate(decided) if hour_of[idx] in EURUSD_BLOCKED_HOURS]
        unblocked = [r for idx, r in enumerate(decided) if hour_of[idx] not in EURUSD_BLOCKED_HOURS]
        blocked_n, unblocked_n = len(blocked), len(unblocked)
        blocked_win = (sum(1 for r in blocked if r["outcome"] == "win") / blocked_n * 100.0) if blocked_n else float("nan")
        unblocked_win = (sum(1 for r in unblocked if r["outcome"] == "win") / unblocked_n * 100.0) if unblocked_n else float("nan")
        gap = unblocked_win - blocked_win if blocked_n and unblocked_n else float("nan")

        print(f"\nEURUSD block (08-18 UTC): n={blocked_n}, win%={blocked_win:.1f}")
        print(f"Outside block (19-07 UTC): n={unblocked_n}, win%={unblocked_win:.1f}")
        print(f"Gap (outside - inside): {gap:+.1f} pts\n")

        summary_rows.append((symbol, len(decided), blocked_n, blocked_win, unblocked_n, unblocked_win, gap))

    print("\n=== Pooled summary across all pairs ===")
    print(f"{'symbol':<8} {'n':>6} {'in_n':>6} {'in_win%':>8} {'out_n':>6} {'out_win%':>9} {'gap':>7}")
    for symbol, n, in_n, in_win, out_n, out_win, gap in summary_rows:
        print(f"{symbol:<8} {n:>6} {in_n:>6} {in_win:>8.1f} {out_n:>6} {out_win:>9.1f} {gap:>+7.1f}")


if __name__ == "__main__":
    sys.exit(main())
