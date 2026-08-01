# Data

This directory will contain reproducible source and processed activity data.

Files:

- `activities.json`: the one canonical, validated catalog used by the
  application. It contains activities from every supported city; `city` and
  `country` control destination filtering.
- `sample_activities.json`: the original Rome fixture, retained for development
  and tests and consolidated into `activities.json`.
- `candidates/*_wikidata_candidates.json`: review-only attraction candidates
  fetched from Wikidata. These do not appear in the application until curated.
- `sources.md`: source, licensing, attribution, and review-boundary notes.

Planned files:

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

Candidate files include Wikidata type labels and review flags. Airports are removed
because they are transport infrastructure, while places such as universities,
libraries, stadiums, and standalone artworks remain for a human to assess in
context.

Do not add secret API responses, personal traveler information, or files whose
licenses do not permit reuse.
