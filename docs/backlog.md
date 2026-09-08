# TripSync MVP backlog

This backlog implements the scope in [plan.md](plan.md). Items are ordered by
dependency and intended delivery sequence.

## Week one: strict MVP

### P0 — Models and persistence

- [ ] **MVP-01: Add backward-compatible planning fields**
  - Add start and end dates, validate one to five consecutive full days, and
    migrate older day-count-only trips safely.
  - Add traveler daily budget band and optional pace/time preferences.
  - Add optional accommodation neighborhood with central-Rome fallback.
  - Add itinerary time blocks and explicit draft/published status.
  - Preserve loading of existing saved trips and versions.

- [ ] **MVP-02: Persist the shared working draft**
  - Store one organizer-controlled editable draft separately from immutable
    published versions.
  - Grant submitted preference members read-only access to the working draft.
  - Ensure member-facing reads exclude individual profiles and sensitive notes.
  - Depends on MVP-01.

### P0 — Core group flow

- [ ] **MVP-03: Simplify the traveler preference form**
  - Require interests, budget band, walking comfort, and dietary needs.
  - Make must-dos, pace, preferred time, and the short note optional.
  - Use one concise submitted form with stable widget keys.
  - Depends on MVP-01.

- [ ] **MVP-04: Allow the organizer to build before every reply**
  - Enable draft creation once two profiles are complete.
  - Show complete and pending travelers clearly.
  - Keep later profile submissions without silently changing an existing draft.
  - Depends on MVP-02 and MVP-03.

### P0 — Planner and publishing

- [ ] **MVP-05: Add block-based scheduling**
  - Place activities into morning, afternoon, or evening blocks.
  - Preserve pace, duration, transition, duplication, and capacity guardrails.
  - Keep the data model extensible for fixed reservation times later.
  - Depends on MVP-01.

- [ ] **MVP-06: Add soft daily budget estimates**
  - Derive the group middle budget band.
  - Define and document a deterministic range for each existing catalog budget
    level.
  - Estimate activity costs plus a general food allowance as ranges.
  - Show group/day warnings without excluding activities or blocking publishing.
  - Depends on MVP-01 and MVP-05.

- [ ] **MVP-07: Complete the draft-to-publish lifecycle**
  - Let the organizer edit the shared working draft.
  - Let members view the latest draft after refreshing.
  - Publish an immutable version and allow a new draft from that version.
  - Block only invalid schedules and required unacknowledged warnings.
  - Depends on MVP-02, MVP-05, and MVP-06.

### P0 — Catalog and release quality

- [ ] **MVP-08: Expand and validate the Rome catalog**
  - Run automated Rome discovery toward roughly 40 published activities.
  - Require complete planning fields and reliable source/location identity.
  - Reject incomplete entries and retain ambiguous entries in the review queue.
  - Verify category, budget, walking, and neighborhood diversity.

- [ ] **MVP-09: Verify the release**
  - Add unit tests for new models, permissions, time blocks, budgets, and publish
    validation.
  - Add itinerary cases for incomplete groups and fairness coverage.
  - Run the full unit suite, deterministic evaluations, health check, and a local
    Streamlit smoke test.
  - Update user-facing documentation after behavior is verified.
  - Depends on MVP-03 through MVP-08.

## Week two: stretch scope

- [ ] **COL-01: Add post-itinerary voting**
  - Store like, neutral, or dislike per member and scheduled item.
  - Show identities only to the organizer and totals to members.

- [ ] **COL-02: Add named traveler proposals**
  - Support add, remove, replace, and move operations.
  - Match free text to the catalog, then allow a labeled unverified custom item.
  - Keep organizer approval authoritative.

- [ ] **COL-03: Add balanced revision suggestions**
  - Combine votes with profiles, fairness, pace, budget, and location.
  - Never mutate the draft automatically.
  - Explain why each revision is suggested.
  - Depends on COL-01 and COL-02.

- [ ] **COL-04: Route member AI requests through the organizer**
  - Queue member requests without making an API call.
  - Let the organizer approve a request before generating grounded proposals.
  - Preserve deterministic validation before any proposal can be applied.

- [ ] **EXP-01: Add concise PDF export**
  - Export published versions only.
  - Include time blocks, activities, locations, warnings, and budget ranges when
    available.
  - Exclude private profiles, votes, and proposals.

- [ ] **EXP-02: Add an optional activity map**
  - Display pins grouped by day.
  - Do not imply optimized routes or travel-time accuracy.

## Post-MVP parking lot

- [ ] Curated restaurant catalog and one named dining recommendation per day.
- [ ] Live hours, transit, routing, and reservation support.
- [ ] Passwordless traveler participation.
- [ ] Partial arrival and departure days.
- [ ] Ten travelers, ten days, and additional destinations.
- [ ] Mobile trip mode, live alerts, and real-time collaboration.

## Definition of done

A backlog item is complete only when its behavior is implemented, permission
boundaries are tested, existing saved data still loads, relevant unit and
evaluation cases pass, and user-facing documentation matches the shipped flow.
