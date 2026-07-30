# Tests

The current suite covers:

- trip, traveler, and activity data validation;
- sample-catalog integrity;
- deterministic text retrieval, destination filtering, fallbacks, and must-do
  inclusion;
- group-fit interest, walking, must-do, budget, and fairness rules;
- deterministic activity ranking;
- deterministic itinerary pacing, shortlist priority, automatic filling,
  deduplication, and unscheduled explanations;
- Streamlit preference, retrieval, must-do, and shortlist interactions.

Run all tests from the repository root:

```bash
.venv/bin/python -m unittest -v
```

The next phase will add activity rejection and individual-day regeneration checks.
