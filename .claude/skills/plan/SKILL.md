---
name: plan
description: Scaffold or update a phased implementation plan under docs/plans/todo/. Used by the architect agent before any non-trivial development work starts.
---

Create or update a plan file at `docs/plans/todo/<slug>.md`, where `<slug>` is a short kebab-case name for the feature/bugfix. Use this template:

```markdown
# <Title>

Status: todo
Triage: low | elevated — <one-sentence justification>

## Goal

<What problem this solves and why, in 2-4 sentences.>

## Out of scope

<What this plan explicitly does not cover.>

## Phases

### Phase 1: <name>

#### Subphase 1.1: <name>

- Change: <exact files/functions to touch>
- Acceptance criteria (test-first): <what a test must assert to prove this subphase works; what must be mocked>

#### Subphase 1.2: <name>

...

### Phase 2: <name>

...

## Open questions

<Anything that needs a human/main-session decision before or during implementation. Leave empty if none.>

## QA

<Left empty — filled in by the qa agent as subphases complete.>
```

Rules for filling it in:

- Every subphase must be independently implementable and testable — a developer agent working from just that subphase's bullet points, with no other context, should be able to build it.
- Order phases and subphases in strict dependency order.
- State acceptance criteria as test assertions, not vague goals — "test asserts X returns `sell` when MACD histogram crosses below zero for 3 consecutive ticks with `N_TICK_CONFIRMATION=3`," not "make sure n-tick confirmation works."
- Fill in the Triage line honestly — see the architect agent's instructions for the criteria. This drives which model the main session uses for deeper work on this plan later.
- Don't leave open design decisions inside a subphase; put them in "Open questions" instead and resolve what you can yourself before finalizing the plan.
