# Evaluation

## Retrieval baseline

The first reproducible benchmark contains eight labeled Rome trip requests in
`retrieval_cases.json`. Each case includes a validated `TripRequest` and one or more
activity IDs considered relevant.

Run the text-retrieval baseline from the repository root:

```bash
.venv/bin/python -m evaluation.retrieval
```

Use `--k` to change the cutoff or `--json` for machine-readable output:

```bash
.venv/bin/python -m evaluation.retrieval --k 3 --json
```

The report includes:

- **Hit Rate@K**: the share of cases with at least one relevant result in the first
  K positions;
- **MRR@K**: the average reciprocal rank of the first relevant result;
- **Mean Recall@K**: the average share of each case's relevant labels retrieved in
  the first K positions;
- per-case first-relevant ranks for debugging misses.

The current deterministic text baseline at `K=5` produces:

| Metric | Score |
|---|---:|
| Hit Rate@5 | 1.000 |
| MRR@5 | 0.938 |
| Mean Recall@5 | 1.000 |

These results are a regression baseline for the current small, curated Rome
catalog—not a claim about real-world retrieval quality. Future vector and hybrid
approaches should run against the same labels, followed by a larger independently
reviewed dataset.

Raw experiments belong in `notebooks/`; reusable metrics and benchmark runners stay
in this directory.

## Itinerary baseline

The itinerary benchmark contains six planning scenarios covering balanced, relaxed,
and packed trips; automatic and shortlist-only filling; active must-dos; compact
must-do text; and an intentionally excluded must-do.

Run it from the repository root:

```bash
.venv/bin/python -m evaluation.itinerary
```

Add `--json` for the complete case-level output:

```bash
.venv/bin/python -m evaluation.itinerary --json
```

The report includes:

- **Constraint pass rate**: trips with the requested day sequence, pace limits,
  unique activities, preserved settings, and no excluded activities;
- **Exclusion compliance**: trips that never schedule rejected or dismissed IDs;
- **Retrieved-candidate grounding**: scheduled activities found in the retrieval
  output rather than invented by the planner;
- **Must-do coverage**: active recognized must-dos that were scheduled;
- **Shortlist coverage**: non-excluded shortlist activities that were scheduled;
- **Traveler coverage**: travelers served by at least one direct interest or
  must-do match;
- **Fairness**: the least-served traveler's matching-activity count divided by the
  most-served traveler's count;
- **Capacity utilization**: planned activity and transition time divided by
  available pace-specific time;
- **Deterministic stability**: scenarios producing identical results on a repeat
  run.

The current six-case deterministic baseline is:

| Metric | Score |
|---|---:|
| Constraint pass rate | 1.000 |
| Exclusion compliance | 1.000 |
| Retrieved-candidate grounding | 1.000 |
| Must-do coverage | 1.000 |
| Shortlist coverage | 1.000 |
| Mean traveler coverage | 1.000 |
| Mean fairness | 0.666 |
| Mean capacity utilization | 0.724 |
| Deterministic stability | 1.000 |

The fairness result is intentionally reported rather than hidden behind the perfect
guardrail scores. For example, the relaxed nature case serves one traveler with
four matching activities and the other with one, producing fairness `0.250`. This
baseline identifies group balancing as the next planner-quality improvement.
