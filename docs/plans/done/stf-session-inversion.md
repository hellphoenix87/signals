# STF session-filter inversion

## Goal

The live session filter blocks 08:00-18:59 UTC -- a window derived for MTF. A W3 production-code
smoke test found that for STF those blocked hours are the *better* trades (-$0.097/trade vs
-$0.204/trade traded). Test whether inverting the block (trade 08-18 UTC only) beats the shipped
filter, and make the filter's UTC conversion DST-correct so both live and backtests block the hours
they claim to.

## Phases

### Phase 1: Pre-register Test M -- DONE
- Change: Test M appended to `docs/test-results/pre-registrations-2026-09-22.md` before any
  screen-window data was fetched. W3 excluded from grading.

### Phase 2: DST-correct session filter -- DONE
- Change: `Config.BROKER_TIMEZONE = "Europe/Athens"`; `SessionFilteredSignalStrategy` converts each
  candle/tick time to UTC with that date's DST rules instead of an offset frozen at startup.
  Precedence: pinned `SESSION_FILTER_UTC_OFFSET_HOURS` > `BROKER_TIMEZONE` > live detection.
- Why: the broker follows EU DST (UTC+2/+3), so the frame-minus-UTC offset is +3 in winter, +5 in
  summer. The frozen offset goes 2h stale at the 25 Oct 2026 change for a bot left running, and
  mislabels every winter backtest candle by 2h.
- Acceptance: frame-minus-UTC = 3 for Feb/early-Mar/Dec timestamps, 5 for 30 Mar-24 Oct; summer
  behaviour identical to the live-measured offset (no live change today). Verified.

### Phase 3: Screen W6/W5/W7/W2 and record the result -- DONE (PASS at every stage, 10/10)
- Change: one unfiltered production run per window; grade SHIPPED vs INVERTED per the
  pre-registration; write `docs/test-results/stf-session-inversion.md`.
- Acceptance: verdict stated against the pre-registered criteria (a)/(b)/(c). On PASS, expansion
  windows W10/W8 are next -- not a Config change.

## Open questions
- If inversion passes all stages: ship as `SESSION_FILTER_BLOCKED_HOURS_UTC_BY_SYMBOL["EURUSD"]`
  = 19-23 + 0-7 (config only, no code). User decision.
