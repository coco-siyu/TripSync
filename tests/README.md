# Tests

The current suite covers:

- trip, traveler, and activity data validation;
- sample-catalog integrity;
- deterministic text retrieval, destination filtering, fallbacks, and must-do
  inclusion;
- group-fit interest, walking, must-do, budget, and fairness rules;
- deterministic activity ranking;
- Streamlit preference, retrieval, must-do, and shortlist interactions.

Run all tests from the repository root:

```bash
.venv/bin/python -m unittest -v
```

Itinerary constraint checks will be added with the itinerary-generation component.
