# Scheduled catalog ingestion

TripSync uses a **quality-gated** ingestion pipeline. It can discover many
places quickly while keeping clear non-activities out of the traveler catalog.

The popular-destination queue lives in `data/destination_queue.json`. It is a
versioned list of city/country pairs that the schedule uses automatically. Set
`"active": false` to pause a place; add another object to expand the queue.

The weekly workflow rotates through three active destinations at a time, in
queue order. It saves a durable cursor in Supabase after each run, so the next
run starts immediately after the last completed destination rather than using
the calendar date. A failed city remains the next starting point for retry.
Manual runs with a rotation size use the same cursor; a normal one-off city run
does not change it.

## What the pipeline does

1. Retrieves bounded place candidates from Wikidata for each requested city.
2. Rejects clear non-visitor records such as hotels, transport, organisations,
   tragic events, and anonymous placeholder records.
3. Converts recognised attractions into conservative, Pydantic-validated
   activity records and upserts only new records into the shared Supabase catalog.
4. Appends the retrieved record and its quality outcome to the local DuckDB
   provenance log through `dlt`.

Ambiguous records such as universities, libraries, stadiums, islands, and
seasonal events are kept in the separate **Needs review** queue. They never
become traveler recommendations until a curator explicitly approves them.
The **Curate catalog** workspace is for editing or deleting published activity
records and for an optional one-off city import.

## Run locally

After installing the requirements:

```bash
python -m src.catalog_dlt --limit 50
```

To preview the same small slice used by the weekly workflow:

```bash
python -m src.catalog_dlt --limit 50 --rotate-size 3
```

This publishes only quality-approved attractions and records provenance in
`data/tripsync_ingestion.duckdb`. The DuckDB file is deliberately ignored by
Git: it is a local audit log, not the source of the traveler-facing catalog.

For an on-demand city outside the queue, use the manual command (the Curation
workspace has the equivalent city-and-country form):

```bash
python -m src.catalog_dlt --cities Paris Lyon --country France --limit 50
```

## Scheduled GitHub workflow

`.github/workflows/catalog-ingestion.yml` runs every Monday at 05:17 UTC and is
also available from **Actions → Refresh quality-screened catalog attractions →
Run workflow**. It rotates through three destinations and writes directly to
the shared catalog. Configure `SUPABASE_URL` and `SUPABASE_SECRET_KEY` as
repository secrets before enabling the scheduled workflow.

If Wikidata is temporarily unavailable for one destination, the workflow logs
the failure, records it in the ingestion history, and leaves the cursor at that
city so a later run can retry it without skipping ahead.
