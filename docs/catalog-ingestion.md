# Scheduled catalog ingestion

TripSync uses a **review-first** ingestion pipeline. It can discover many places
quickly, but it never lets a source or an LLM silently publish travel advice.

The popular-destination queue lives in `data/destination_queue.json`. It is a
versioned list of city/country pairs that the schedule uses automatically. Set
`"active": false` to pause a place; add another object to expand the queue.

## What the pipeline does

1. Retrieves bounded place candidates from Wikidata for each requested city.
2. Saves each city's raw candidates to `data/candidates/` for the Curation workspace.
3. Appends retrieval provenance to a local DuckDB file through `dlt`.
4. Opens a GitHub pull request containing only the updated candidate JSON files.

The workflow does **not** add anything to the published activity catalog. An
organizer still reviews the batch, edits any planning estimates, and explicitly
publishes eligible activities in **Curate catalog**.

## Run locally

After installing the requirements:

```bash
python -m src.catalog_dlt --limit 50
```

This creates or refreshes review batches and local provenance in
`data/tripsync_ingestion.duckdb`. That DuckDB file is deliberately ignored by
Git: it is a local audit log, while the reviewable candidate JSON is what gets
versioned and proposed in a pull request.

For an on-demand city outside the queue, use the manual command (the Curation
workspace has the equivalent city-and-country form):

```bash
python -m src.catalog_dlt --cities Paris Lyon --country France --limit 50
```

## Scheduled GitHub workflow

`.github/workflows/catalog-ingestion.yml` runs every Monday at 05:17 UTC and is
also available from **Actions → Refresh review-only catalog candidates → Run
workflow**. It creates or updates a branch named `automation/catalog-candidates`
and opens a pull request. Review the candidates in the app before accepting any
into the shared catalog.

If Wikidata is temporarily unavailable, the workflow fails visibly rather than
writing a partial or invented batch. Re-run it later from GitHub Actions.
