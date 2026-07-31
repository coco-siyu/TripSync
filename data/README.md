# Data

This directory will contain reproducible source and processed activity data.

Files:

- `sample_activities.json`: a small, validated Rome catalog for development and
  tests.
- `candidates/*_wikidata_candidates.json`: review-only attraction candidates
  fetched from Wikidata. These do not appear in the application until curated.
- `sources.md`: source, licensing, attribution, and review-boundary notes.

Planned files:

- `activities.json`: the expanded, validated activity catalog used by the
  application;
- `sources.md`: source, attribution, and licensing notes for the expanded catalog.

Validate the sample catalog from the repository root:

```bash
python -m unittest tests.test_models
```

Fetch candidate places for all four initial cities:

```bash
python -m src.catalog_import
```

Or fetch one smaller review batch:

```bash
python -m src.catalog_import --cities Florence --limit 25
```

The importer writes to `data/candidates/` by default. Candidates are deliberately
not converted into activities automatically: review the planning fields described
in [`sources.md`](sources.md) before adding an item to `activities.json`.

Do not add secret API responses, personal traveler information, or files whose
licenses do not permit reuse.
