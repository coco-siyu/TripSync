# Deterministic itinerary planning

TripSync converts the organizer’s shortlist and the grounded group-fit ranking into
a stable day-by-day plan. The deterministic planner establishes constraint-safe
behavior before an LLM is used for narrative generation.

## Planning order

1. Recognized must-dos that remain in the shortlist.
2. Other activities explicitly saved in the shortlist.
3. Score-ranked retrieved recommendations, when automatic filling is enabled.

Removing a must-do from the shortlist is treated as an intentional organizer
override. The planner does not silently add it back.

## Rejection and same-day replacement

Each scheduled activity can be rejected with a structured reason and an optional
note. TripSync records the feedback, removes the activity from the shortlist, and
tries to replace it on the same day with the highest-ranked eligible activity that
still fits that day's pace limits.

- Previously rejected activities are excluded from automatic filling and future
  replacements.
- Only the affected day is recalculated; all other days remain unchanged.
- A rejected must-do is treated as an intentional organizer override.
- If no eligible activity fits, the day keeps an explained open slot instead of
  violating its limits.
- The latest replacement can be undone, restoring the exact itinerary, shortlist,
  must-do, and rejection state from before the change.

Rejected activities remain available in the **Rejected** results view. Restoring
one removes its rejection record and adds it back to the shortlist; the organizer
then rebuilds the itinerary so the planner can place it safely.

## Pace limits

| Pace | Daily activity time | Maximum activities |
|---|---:|---:|
| Relaxed | 4 hours | 2 |
| Balanced | 6 hours | 3 |
| Packed | 8 hours | 4 |

The daily total includes a 30-minute transition estimate between activities.
Activities are distributed across the least-loaded eligible day, with the day
number used as a stable tie-breaker.

## Guardrails

- Only retrieved catalog activity IDs can be scheduled.
- An activity appears at most once.
- The trip always contains the requested one to five sequential days.
- No day exceeds its pace-specific duration or activity-count limit.
- Shortlisted activities that cannot fit are retained in an unscheduled section
  with an explanation.
- Missing shortlist IDs are reported rather than invented.
- Rejected and dismissed must-do IDs cannot be silently reintroduced.
- Changing the shortlist or automatic-fill setting invalidates the generated plan
  until the organizer rebuilds it.

## Current boundaries

The schedule does not yet use live opening hours, ticket availability, travel
times, meal windows, or route optimization. The UI labels its transition timing as
an estimate.

An LLM can later add readable day summaries and trade-off explanations, but it
should receive this validated plan as grounded input and must not introduce
activities outside the retrieved catalog.

## Evaluation baseline

Six reproducible planning scenarios measure hard-constraint compliance, exclusions,
retrieved-candidate grounding, must-do and shortlist coverage, traveler coverage,
fairness, capacity utilization, and deterministic stability.

Run the benchmark with:

```bash
.venv/bin/python -m evaluation.itinerary
```

All current scenarios pass the hard constraints, exclusions, grounding, coverage,
and stability checks. Mean fairness is `0.666`, which reveals that direct-interest
coverage can still favor one traveler even when everybody receives at least one
match. See [evaluation/README.md](../evaluation/README.md) for metric definitions
and the complete baseline.
