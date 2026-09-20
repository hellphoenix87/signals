"""One-off analysis for Thread 2 of `docs/exit-strategy-open-threads.md`:
does a pre-breakeven soft-SL threshold conditional on M5-confirm state
(whether RSI/M5 actually agrees with the trade direction, vs. just
neutral) beat today's flat `Config.EXIT_MAX_LOSS_MONEY` ($5) rule.

Reads the per-trade CSVs produced by
`scripts/backtest_exit_strategy.py --sweep-pre-be-threshold` (each row:
metadata including `m5_confirm`, plus `pre<C>_outcome/profit/ticks` for
every candidate in `PRE_BE_THRESHOLD_CANDIDATES`), splits trades into
"m5 confirms" (`m5_confirm` truthy and not "neutral"/"none") vs. "m5
doesn't confirm" (everything else), and for each candidate threshold
reports, per group: total pre-BE-phase P&L, win rate, and reached-BE
rate -- so the best threshold per group can be read directly and
compared against today's flat $5 baseline applied uniformly.

Usage:
    PYTHONPATH=. pipenv run python scripts/analyze_pre_be_threshold_by_m5_confirm.py \
        backtest_results/EURUSD_pre_be_threshold_sweep_*.csv backtest_results/GBPUSD_pre_be_threshold_sweep_*.csv ...

Each CSV's pair is recovered from its own filename
(`<SYMBOL>_pre_be_threshold_sweep_<timestamp>.csv`, exactly what
`backtest_exit_strategy.py` writes) purely for the printed row counts;
all pairs are pooled together for the actual per-group comparison,
since Thread 2's own evidence (confidence/full-agreement/M5-confirm
correlations) was already established pooled across all 7 pairs, not
per-pair.
"""

import argparse
import csv
import sys
from pathlib import Path

CANDIDATES = [2.0, 3.0, 4.0, 5.0, 7.0, 10.0]
CURRENT_LIVE_THRESHOLD = 5.0


def label_for(c: float) -> str:
    return f"pre{str(c).replace('.', '_')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_files", nargs="+", help="Per-trade CSVs from backtest_exit_strategy.py --sweep-pre-be-threshold")
    return parser.parse_args()


def symbol_from_filename(path: Path) -> str:
    return path.name.split("_pre_be_threshold_sweep_")[0]


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def m5_confirms(row: dict) -> bool:
    """`m5_confirm` holds a raw direction string ("hold"/"buy"/"sell"),
    not a boolean -- confirmation means it actually agrees with the
    trade's own `direction`, not merely that it's non-"hold". Per
    Thread 2's own established caveat, `m5_confirm` is effectively
    2-state in practice (agrees/holds -- "opposes" never occurs given
    the MTF veto logic already in place), so this simplifies to "equals
    direction" without needing to handle a real opposing case."""
    m5 = (row.get("m5_confirm") or "").strip().lower()
    direction = (row.get("direction") or "").strip().lower()
    return bool(m5) and m5 == direction


def summarize_group(rows: list[dict], label: str) -> None:
    n = len(rows)
    print(f"\n=== {label} (n={n}) ===")
    if n == 0:
        print("  (no trades)")
        return

    print(f"{'threshold':>10} {'total_pnl':>12} {'win%':>7} {'reached_be':>12}")
    for c in CANDIDATES:
        col = label_for(c)
        profits = [float(r[f"{col}_profit"]) for r in rows if r.get(f"{col}_profit") not in (None, "")]
        outcomes = [r[f"{col}_outcome"] for r in rows if r.get(f"{col}_outcome")]
        total_pnl = sum(profits)
        win_rate = (sum(1 for p in profits if p > 0) / len(profits) * 100.0) if profits else 0.0
        reached_be = sum(1 for o in outcomes if o == "reached_be")
        marker = "  <-- current" if c == CURRENT_LIVE_THRESHOLD else ""
        print(f"${c:>8.1f} ${total_pnl:>10.2f} {win_rate:>6.1f}% {reached_be:>7d}/{n}{marker}")


def main() -> None:
    args = parse_args()

    all_rows: list[dict] = []
    counts_by_symbol: dict[str, int] = {}
    for raw_path in args.csv_files:
        path = Path(raw_path)
        rows = load_rows(path)
        all_rows.extend(rows)
        symbol = symbol_from_filename(path)
        counts_by_symbol[symbol] = counts_by_symbol.get(symbol, 0) + len(rows)

    print("Rows loaded per pair:")
    for symbol, n in sorted(counts_by_symbol.items()):
        print(f"  {symbol}: {n}")
    print(f"Total trades pooled: {len(all_rows)}")

    confirm_rows = [r for r in all_rows if m5_confirms(r)]
    non_confirm_rows = [r for r in all_rows if not m5_confirms(r)]

    summarize_group(all_rows, "All trades (pooled baseline)")
    summarize_group(confirm_rows, "M5 confirms")
    summarize_group(non_confirm_rows, "M5 does NOT confirm")

    print("\n=== Verdict inputs ===")
    for group_rows, group_label in ((confirm_rows, "M5 confirms"), (non_confirm_rows, "M5 does NOT confirm")):
        if not group_rows:
            continue
        best_c, best_pnl = None, None
        current_pnl = None
        for c in CANDIDATES:
            col = label_for(c)
            profits = [float(r[f"{col}_profit"]) for r in group_rows if r.get(f"{col}_profit") not in (None, "")]
            total_pnl = sum(profits)
            if c == CURRENT_LIVE_THRESHOLD:
                current_pnl = total_pnl
            if best_pnl is None or total_pnl > best_pnl:
                best_pnl, best_c = total_pnl, c
        gain = (best_pnl - current_pnl) if (best_pnl is not None and current_pnl is not None) else None
        print(
            f"{group_label}: best=${best_c:.1f} (${best_pnl:+.2f}) vs current $5.0 (${current_pnl:+.2f}) "
            f"-> {'+' if gain and gain > 0 else ''}{gain:+.2f} if switched" if gain is not None else f"{group_label}: insufficient data"
        )


if __name__ == "__main__":
    sys.exit(main())
