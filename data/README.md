# Data

This directory will contain reproducible source and processed activity data.

Files:

- `sample_activities.json`: a small, validated Rome catalog for development and
  tests.

Planned files:

- `activities.json`: the expanded, validated activity catalog used by the
  application;
- `sources.md`: source, attribution, and licensing notes for the expanded catalog.

Validate the sample catalog from the repository root:

```bash
python -m unittest tests.test_models
```

Do not add secret API responses, personal traveler information, or files whose
licenses do not permit reuse.
