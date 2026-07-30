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
- Changing the shortlist or automatic-fill setting invalidates the generated plan
  until the organizer rebuilds it.

## Current boundaries

The schedule does not yet use live opening hours, ticket availability, travel
times, meal windows, or route optimization. The UI labels its transition timing as
an estimate.

An LLM can later add readable day summaries and trade-off explanations, but it
should receive this validated plan as grounded input and must not introduce
activities outside the retrieved catalog.
