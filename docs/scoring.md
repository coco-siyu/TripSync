# Group-fit scoring

TripSync initially ranks activities with deterministic rules so scores can be
explained, tested, and adjusted before an LLM is involved.

## Per-traveler score

Each activity receives a score from 0 to 100 for every traveler:

| Component | Maximum | Rule |
|---|---:|---|
| Interest match | 55 | 0 for no overlap, 40 for one shared interest, 55 for two or more |
| Walking compatibility | 25 | 25 when the activity is within tolerance, otherwise 0 |
| Must-do match | 20 | 20 when the activity name or stable ID matches a must-do entry |

The score includes structured explanations and matched interests. Walking conflicts
are retained as visible trade-offs rather than silently ignored.

## Group score

The group score combines:

```text
65% × average traveler score
20% × lowest traveler score
15% × group budget compatibility
```

The lowest traveler score is the fairness component. It reduces the ranking of an
activity that strongly serves one traveler while offering little value to another.

Budget compatibility is scored as:

- 100 when the activity is within the selected budget;
- 50 when it is one level above the selected budget;
- 0 when it is two or more levels above the selected budget.

Results also include traveler coverage, per-traveler evidence, budget compatibility,
and explicit trade-offs. Activities are sorted by total score, then traveler
coverage, activity name, and stable ID so rankings are reproducible.

## Current boundaries

- Candidates must match the trip city and country.
- Walking conflicts are penalized and surfaced, but do not automatically remove an
  activity. This preserves transparent trade-offs for group review.
- Prices, opening hours, routes, and live availability are outside this scoring
  phase.
