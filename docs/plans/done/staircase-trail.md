# Staircase Profit Trail

Status: done

## Goal

Replace `ProfitExitManager`'s percentage-of-peak post-breakeven profit trail with a
staircase/ratchet trail: as the running peak crosses each `$2` tier, that tier becomes the new
stop. Ported from the backtest-only `--staircase-trail` mode in
`scripts/backtest_exit_strategy.py` into production.

## Evidence

The original investigation (`docs/test-results/exit-strategy-reactive-and-staircase-investigation.md`)
found the `$2` staircase beat the live trail on W1-W2 but explicitly gated it: *"Only validated on
W1-W2; confirm on W3/W4 at the live cap before relying on it."* That confirmation was run on
2026-09-24 and it holds -- 4/4 windows.

Both W3/W4 runs: single-timeframe, live `$1` post-BE cap, 10k ticks, windows pinned by date so the
two arms are exactly comparable.

| Window | Baseline | Staircase | Delta |
|---|---|---|---|
| W3 (2026-06-30 -> 2026-07-28, 3,625 trades) | -$1,499.40 | **-$1,118.60** | **+$380.80** |
| W4 (2026-06-02 -> 2026-06-30, 3,827 trades) | -$3,023.40 | **-$2,783.80** | **+$239.60** |

Stronger than the headline on two counts:

- **The `exhausted` bucket vanishes.** Baseline leaves 29 (W3) / 15 (W4) trades still open at the
  10k-tick cutoff, credited +$469.60 / +$363.80 -- which the investigation flags as *systematically
  optimistic*. The staircase has zero. Like-for-like, the deltas are **+$850.40** and **+$603.40**.
- **The mechanism matches the claim.** More trades captured, at a higher average, far faster:
  W3 969 @ +$1.52 over 1,140 ticks -> 1,074 @ +$2.06 over 323 ticks; W4 776 @ +$1.66 over 827
  ticks -> 869 @ +$2.06 over 193 ticks.

Clean A/B: `failed_to_reach_be` and `profit_drop` are identical to the dollar across both arms in
both windows (190/-$214.00 and 26/-$334.00 in W3; 742/-$1,166.40 and 112/-$813.80 in W4). Only the
post-BE trail changed.

## What this does NOT fix

**Every window is still net negative.** The staircase reduces bleed; it does not make the system
profitable. The post-BE `$1` cap remains where the money goes (-$2,787 in W3, -$2,591 in W4 even
with the staircase), and the trail is the one component already running net positive.

The staircase is inactive below `$2` peak, exactly like the trail it replaces. In W3, 2,333 of
3,409 break-even-reaching trades (68%) peaked at or below `$2` and were decided entirely by the
cap -- the staircase never sees them. It improves the profitable third of the book, not the
two-thirds doing the damage.

`EXIT_STAIRCASE_FIRST_TIER` is deliberately left at the default (= tier width, so tiers at
$2/$4/$6...). Activating earlier was tested and was **worse**: first tier at `$0.5` gave
W1 -$646 -> -$846 and W2 -$630 -> -$789, despite a higher win rate.

## Out of scope

- Retuning the post-BE `$1` cap against the new trail shape. The backtest has
  `--sweep-staircase-cap` for exactly this, and it is the more promising lever given the numbers
  above -- but it is a separate change with its own validation burden.
- Anything in the pre-breakeven phase (the `$5` soft SL, the 30-tick arming timeout). Untouched.

## Phases

### Phase 1: Config

- Change: add to `app/config/settings.py::Config` -- `EXIT_STAIRCASE_TRAIL_ENABLED: bool = True`,
  `EXIT_STAIRCASE_TIER_WIDTH: float = 2.0`, `EXIT_STAIRCASE_FIRST_TIER: float = 0.0` (0 meaning
  "same as tier width", the validated shape). Add the matching lowercase fields to
  `ExitTradeConfig` in `app/exit_strategies/exit_trade.py`, reading from `Config` the same way its
  neighbours do.
- Acceptance criteria: `ExitTradeConfig().staircase_trail_enabled/.staircase_tier_width/
  .staircase_first_tier` resolve to `True`/`2.0`/`0.0`. The existing `EXIT_TRAIL_GAP_*` settings
  stay in place -- they are the fallback when the staircase is disabled, not dead config.

### Phase 2: Implement the ratchet in `ProfitExitManager`

- Change: `app/exit_strategies/managers/profit.py`. Add `_staircase_trigger(peak)` returning the
  highest fully-crossed tier (`first + floor((peak - first) / width) * width`), or `None` when the
  staircase is inactive for that peak (`width <= 0`, or `peak < first`). Add `_trail_trigger(peak)`
  returning `(trigger, reason)` -- the staircase tier with `reason="staircase_trail_breach"` when
  enabled, else the existing `peak - _trail_gap(peak)` with
  `reason="trailing_breach_pct_of_peak"`. Use it in both `check_exit_on_tick` and
  `check_exit_on_candle_close`, whose trail blocks are currently identical.
- Acceptance criteria: semantics match `simulate_full_lifecycle_staircase_trail` exactly -- fires
  only while `0 < profit < best_profit`, only once `best_profit >= first_tier`, only when
  `tier > 0`, and exits when `profit <= tier`. Disabling the flag restores the current
  percentage-of-peak behavior byte-for-byte. Break-even arming, best-profit tracking and the HTF
  gate on the candle-close path are untouched.

### Phase 3: Record the evidence

- Change: new `docs/test-results/staircase-trail-w3-w4-confirmation.md` with the four runs, the
  exit-reason breakdowns, and the caveats above. Update
  `docs/test-results/exit-strategy-reactive-and-staircase-investigation.md`'s open item to point at
  it, and note in `docs/tech-debt.md` that `EXIT_POST_BE_LOSS_CAP_MONEY` retuning against the new
  trail is the open follow-up.
- Acceptance criteria: the numbers in the doc match the raw run output, and the doc states plainly
  that the system remains net negative on all four windows.

## Open questions

- This is backtest evidence only. Per item 6 of `docs/tech-debt.md`, a live smoke run confirming
  `staircase_trail_breach` actually appears in the logs would close the loop -- but it needs a real
  position to open, which the 2026-09-24 smoke run never got.
