# Tests

The current suite covers:

- trip, traveler, and activity data validation;
- sample-catalog integrity;
- deterministic text retrieval, destination filtering, fallbacks, and must-do
  inclusion;
- retrieval Hit Rate, reciprocal-rank, recall, label-integrity, and reproducibility
  checks;
- group-fit interest, walking, must-do, budget, and fairness rules;
- deterministic activity ranking;
- deterministic itinerary pacing, shortlist priority, automatic filling,
  deduplication, and unscheduled explanations;
- deterministic same-day replacement, rejection exclusions, and no-fit open slots;
- itinerary constraint, grounding, coverage, fairness, utilization, and
  reproducibility metrics;
- Streamlit preference, retrieval, must-do, shortlist, rejected-activity restore,
  and replacement-undo interactions.

Run all tests from the repository root:

```bash
.venv/bin/python -m unittest -v
```

The next phase will add itinerary-quality evaluation and persistent feedback
storage.
