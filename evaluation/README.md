# Evaluation

## Retrieval baseline

The reproducible benchmark contains labeled Rome, Florence, and Milan trip
requests in `retrieval_cases.json`. Each case follows the same validated
`TripRequest` and relevant-activity-ID contract; only its destination-grounded
labels change. See [`docs/curation-standard.md`](../docs/curation-standard.md).

Run the text-retrieval baseline from the repository root:

```bash
.venv/bin/python -m evaluation.retrieval
```

Use `--k` to change the cutoff or `--json` for machine-readable output:

```bash
.venv/bin/python -m evaluation.retrieval --k 3 --json
```

Compare all three retrieval strategies on exactly the same cases and labels:

```bash
.venv/bin/python -m evaluation.retrieval --compare
```

The report includes:

- **Hit Rate@K**: the share of cases with at least one relevant result in the first
  K positions;
- **MRR@K**: the average reciprocal rank of the first relevant result;
- **Mean Recall@K**: the average share of each case's relevant labels retrieved in
  the first K positions;
- per-case first-relevant ranks for debugging misses.

The current cross-city deterministic text baseline at `K=5` produces:

| Metric | Score |
|---|---:|
| Hit Rate@5 | 1.000 |
| MRR@5 | 0.917 |
| Mean Recall@5 | 0.819 |

These results are a regression baseline for the current small, curated catalog—not
a claim about real-world retrieval quality. Future vector and hybrid
approaches should run against the same labels, followed by a larger independently
reviewed dataset.

The current local-model comparison at `K=5` is:

| Mode | Hit Rate@5 | MRR@5 | Mean Recall@5 |
|---|---:|---:|---:|
| Text | 1.000 | 0.917 | 0.819 |
| Vector | 1.000 | 0.917 | 0.958 |
| Hybrid | 1.000 | 0.958 | 0.875 |

Hybrid puts a relevant result first more often; vector returns more of the
benchmark's labeled relevant options in its first five. Keep both measurements as
the catalog grows rather than declaring a winner from this small benchmark.

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
| Mean fairness | 0.704 |
| Mean capacity utilization | 0.764 |
| Deterministic stability | 1.000 |

The fairness result is intentionally reported rather than hidden behind the perfect
guardrail scores. For example, the relaxed nature case serves one traveler with
four matching activities and the other with one, producing fairness `0.250`. This
baseline identifies group balancing as the next planner-quality improvement.

## LLM contract baseline

`llm_cases.json` contains 20 fixed scenarios: 10 itinerary-narration cases and
10 plain-language adjustment requests. These are curated ground-truth cases for
the application's **hard behavioral contract**, rather than ground truth for one
specific piece of travel prose.

The free fixture run verifies that every reference response:

- conforms to the Pydantic structured-output schema;
- preserves the exact itinerary activities and day ordering for narration;
- returns complete narration for every day and scheduled activity; and
- offers at least one adjustment that the deterministic planner can apply within
  the same day's constraints.

Run the reproducible fixture baseline:

```bash
.venv/bin/python -m evaluation.llm
```

Run the optional live version after reviewing API cost. It sends 20 real requests
to the configured OpenAI model and reports the same contract metrics:

```bash
.venv/bin/python -m evaluation.llm --live
```

Use `--json` with either mode for a machine-readable report. A live pass proves
contract compliance for that run; human review is still needed to assess creative
quality, factual nuance, and whether an option is genuinely useful to travelers.
The normal text report also lists every case that needs review, including the
individual check results and any captured error message.

## Held-out LLM robustness baseline

`llm_holdout_cases.json` is kept separate from the 20-case contract suite. It has
12 cases with unfamiliar, informal, ambiguous, conflicting, and unsupported
requests. It deliberately reuses only valid catalog-grounded Rome itinerary plans:
the benchmark tests whether the model stays within TripSync's constraints, rather
than whether it can invent activities for another city.

Run its free structural check:

```bash
.venv/bin/python -m evaluation.llm --cases evaluation/llm_holdout_cases.json
```

Run the paid live robustness check separately:

```bash
.venv/bin/python -m evaluation.llm --cases evaluation/llm_holdout_cases.json --live
```

Do not revise the prompt to fix individual held-out failures. Record the results
separately from the development contract baseline, then use recurring patterns to
decide whether a broader product change is needed.

For example, repeated suggestions that duplicate an already scheduled activity
are addressed by passing the model only `eligible_replacement_activities`, while
the deterministic grounding validator remains the final safeguard.
