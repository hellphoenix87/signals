# Run STF Live (disable MTF gating)

Status: in-progress

## Goal

Set `Config.USE_MULTI_TIMEFRAME_SIGNALS = False` so the live bot runs the single-timeframe (STF)
strategy instead of `MultiTimeframeStrongSignalStrategy`.

## Why

**The live config did not match the research or the validation evidence.** MTF was enabled for live
trading in PR #46 and never revisited, while research moved to STF. Two concrete mismatches found
on 2026-09-25:

1. `docs/tech-debt.md` item 5 states outright that "MTF research was abandoned in favour of STF",
   yet `USE_MULTI_TIMEFRAME_SIGNALS` was still `True`.
2. The `$2` staircase trail shipped in PR #72 was validated on **four STF windows**
   (`--single-timeframe`). The live bot was running MTF, so the shipped exit change had never been
   measured on the strategy actually executing. After this change, live wiring and the staircase
   evidence finally describe the same system.

Research support for STF over MTF (`docs/test-results/pre-be-phase-and-entry-signal-investigation.md`):

- MTF gating "costs 30x the sample size and picks worse trades" in 10 of 12 windows.
- "Its smaller total loss comes from abstaining, not selecting."
- MTF yields ~25 trades per 4-week window, versus 3,625 for STF on the same window.

## The cost, stated plainly

**This will increase absolute bleed, not reduce it.**

| | MTF (before) | STF (after) |
|---|---|---|
| Trades / 4-week window | ~25 | 3,625 |
| Per trade (W3, with staircase) | -- | -$0.31 |
| Total / window | roughly -$25 | **-$1,119** |

~145x the trade count at a negative per-trade expectancy, i.e. roughly 40-45x the loss per window.

Two claims about STF must not be conflated:

- **STF is the better research vehicle** -- supported. 3,625 trades vs 25 is the difference between
  a measurable result and noise, which is why every surviving finding came from STF windows.
- **STF is the better live config** -- NOT supported by any window tested. It picks marginally
  better trades and then takes 145x more of them at a loss.

This change is made deliberately, with the user's explicit decision, on a demo account, to get
statistically meaningful live feedback rather than to reduce losses. It should be revisited the
moment per-trade expectancy is the thing being optimised.

## Out of scope

- **`USE_SESSION_FILTER` stays `True`.** Worth a separate decision, and the reasoning is
  counter-intuitive. The filter blocks 08:00-18:59 UTC and was built for MTF;
  `docs/test-results/session-filter-analysis.md` found single-timeframe has no session effect at
  all (pooled ~42.5% win rate both inside and outside the blocked hours), so for STF it removes
  trades that are no worse than the ones it keeps. That makes it pure opportunity cost *if* the
  system were profitable -- but while per-trade expectancy is negative, blocking 11 hours a day
  **reduces** absolute loss. Leaving it on is the conservative choice; turning it off is a separate,
  deliberate call.
- Retuning anything against STF's trade population (thresholds, the post-BE cap, tier width). The
  staircase and cap were already tuned on STF windows, so they carry over; the MTF-specific
  settings (`MTF_*`) simply stop being read.

## Phases

### Phase 1: Flip the flag

- Change: `app/config/settings.py` -- `USE_MULTI_TIMEFRAME_SIGNALS = True` -> `False`, with a
  comment recording why (research direction + matching the staircase evidence) and the cost
  (~145x trade count, higher absolute bleed). Keep every `MTF_*` setting in place: they become
  unread rather than wrong, and flipping back must stay a one-line change.
- Acceptance criteria: `strategy_factory(config=Config, symbol="EURUSD")` no longer contains
  `MultiTimeframeStrongSignalStrategy` anywhere in its wrapper chain. `SessionFilteredSignalStrategy`
  is still the outermost wrapper (unchanged by this plan). Suite stays green.

### Phase 2: Record the mismatch that caused this

- Change: note in `docs/tech-debt.md` that live config drifted from the research direction for
  multiple PRs, and that the staircase trail was validated on a strategy the bot was not running.
  Add a line to `docs/test-results/staircase-trail-w3-w4-confirmation.md` stating that its STF
  windows now match live wiring as of this change.
- Acceptance criteria: a reader of either doc can see which strategy the live bot runs and which
  strategy a given result was measured on, without having to read `settings.py`.

## Open questions

- `USE_SESSION_FILTER` (above) -- keep blocking 11 hours a day under STF, or not?
- MTF is not deleted, only disabled. If STF is now the committed direction, the `MTF_*` config
  block, `MultiTimeframeStrongSignalStrategy`, and tech-debt item 5's caching optimisation are all
  dead weight. Worth deciding whether MTF is "off for now" or "gone".
