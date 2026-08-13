# TripSync

TripSync is a group travel planner for people who may have different things in minds. It collects everyone's interests, walking comfort, budget, food needs,
pace, and must-dos, then turns them into activity recommendations and an
itinerary the group can review together. Expects to reduce time and friction in the planning process.

The current version of TripSync was built to meet the LLM Zoomcamp project requirements. It is still an early prototype, and I plan to add more features and improve the experience over time.

**Live demo:** [tripsync.streamlit.app](https://tripsync.streamlit.app/)

For the demo, the password for the Feedback insights and Curate catalog pages is:

```bash
admin
```

## Why I built it

The idea came from planning trips with family and friends. Getting everyone to
agree on a pace, budget, food needs, and a few non-negotiable places can be more
difficult than picking the destination. I wanted one shared place where people
could put those preferences down, see the trade-offs, and make a plan together.

It retrieves from a curated activity
catalog first, uses deterministic rules for the actual planning decisions, and
uses an LLM for a grounded trip story and adjustment ideas. The LLM has to return
structured output, and the app checks it before showing anything to the user.

## What it does

- Supports groups of 2 to 6 travelers and trips of 1 to 5 days.
- Searches a curated catalog for Rome, Florence, Milan, and Naples with text,
  vector, or hybrid retrieval.
- Ranks activities by shared interests, walking and budget fit, must-dos, and
  whether the plan treats the group fairly.
- Lets users look through the top five, must-dos, all activities, and rejected
  activities before building an itinerary.
- Builds the itinerary with rules for pace, duration, transitions, and duplicate
  stops.
- Lets the organizer reject, replace, or add activities. If an option pushes a
  day beyond its pace limit, the app asks for confirmation and labels it clearly.
- Uses the OpenAI Responses API for a grounded trip story and optional adjustment
  ideas. The LLM cannot change a plan without the organizer approving it.
- Stores trips, LLM feedback, and overall ratings in Supabase when it is set up.
  SQLite is the fallback for local development.
- Includes an admin-protected feedback dashboard with five charts and a JSON
  export.

## Local setup

Requires Python 3.12 or later.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Set `OPENAI_API_KEY` in `.env` to enable trip stories and LLM adjustment ideas.
You can use the recommendation and itinerary flow without an API call. Never
commit `.env` or `.streamlit/secrets.toml`.

### Shared persistence and feedback dashboard

To keep trips and feedback between app restarts, create a Supabase project and
run [`supabase/schema.sql`](supabase/schema.sql) once in its SQL Editor. Then add
these values to your local `.env` file and to Streamlit Cloud secrets:

```bash
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=your-server-side-secret
TRIPSYNC_ADMIN_PASSWORD=choose-a-dashboard-password
```

The first two values store saved trips, feedback, and the shared activity catalog.
The password protects the Feedback insights and Curate catalog pages. Without
Supabase credentials, TripSync uses local SQLite data in `data/`.

Saved plans belong to the current anonymous browser session. This keeps one
visitor from seeing another visitor's plans. Keeping a person's history across
devices would need authentication.

## Run with Docker Compose

Docker Compose starts the local app and keeps the SQLite fallback state in a
named volume. It also passes through any OpenAI, Supabase, and dashboard values
from `.env`.

```bash
cp .env.example .env
docker compose up --build
```

Open <http://localhost:8501>. Stop it with `Ctrl+C`, or add `-d` to run it in
the background. Only remove the local fallback volume if you want to erase it:

```bash
docker compose down --volumes
```

## Run with Docker

Build the image (the semantic retrieval model is downloaded during the build):

```bash
docker build -t tripsync .
```

Run it with an OpenAI key and a named volume for local fallback state:

```bash
docker run --rm -p 8501:8501 \
  --env-file .env \
  -v tripsync-state:/app/state \
  tripsync
```

Open <http://localhost:8501>. The image includes the catalog. The mounted
`tripsync-state` volume stores only SQLite fallback state. On a hosted platform,
set `OPENAI_API_KEY`, and optionally `OPENAI_MODEL`, Supabase credentials, and
the dashboard password, through that platform's secrets manager. Do not put them
in the repository.

## Evaluation

From an activated virtual environment:

```bash
# Retrieval: text, vector, and hybrid on the same labeled cases
python -m evaluation.retrieval --compare

# Deterministic itinerary constraints, grounding, coverage, and fairness
python -m evaluation.itinerary

# 20 LLM contract fixtures, with no API cost
python -m evaluation.llm

# Optional paid robustness run against held-out prompts
python -m evaluation.llm --cases evaluation/llm_holdout_cases.json --live

# Full test suite
python -m unittest discover -s tests -q
```

The retrieval and itinerary baseline results are in
[evaluation/README.md](evaluation/README.md). The 20 contract cases and 12
held-out LLM robustness cases check schema compliance, grounding, completeness,
and whether a proposal can be applied. They do not try to score how pleasant the
travel writing sounds.

## Data and curation

`data/activities.json` is the seed and local fallback for the validated activity
catalog. When Supabase is configured, the first catalog access copies this file
into the shared `catalog_activities` table. Later additions and deletions remain
there across Streamlit Cloud restarts. Historical candidate files in
`data/candidates/` are optional audit artifacts; they are not required for
ingestion. The curation rules are in
[docs/curation-standard.md](docs/curation-standard.md), and retrieval behavior
is in [docs/retrieval.md](docs/retrieval.md).

### One-off city import

The Curate catalog workspace is primarily for managing the live catalog: filter
published activities by country and city, edit their planning fields, and delete
selected records after confirming the action. Its optional city form is a direct
import: clear, Pydantic-validated attractions are published immediately, while
hotels, airports, transport, organisations, tragic events, and uncertain places
are not added.

For an on-demand city outside the scheduled destination queue, use:

```bash
python -m src.catalog_dlt --cities Venice --country Italy --limit 50
```

The automation filters clear non-activities and publishes only recognised,
Pydantic-valid attractions. It does not claim that a place is currently open,
accessible, or right for every traveler.

### Scheduled candidate retrieval

The project also has a `dlt` ingestion pipeline and a weekly GitHub Actions
workflow. Its versioned destination queue starts with Italian cities, Paris,
Barcelona, London, Kyoto, and New York City. The workflow rotates through three
active destinations, filters obvious non-visitor records, records provenance in
DuckDB, and publishes only clear, Pydantic-valid attractions to the shared
catalog. A manual run processes the entire queue.

```bash
python -m src.catalog_dlt --limit 50
```

See [docs/catalog-ingestion.md](docs/catalog-ingestion.md) for the quality gate,
the scheduled workflow, and how to run a different country.

## Project evidence

- Public demo: [tripsync.streamlit.app](https://tripsync.streamlit.app/)
- CI: GitHub Actions runs the full unit suite on pushes and pull requests,
  including catalog review pull requests.
- RAG boundary: [docs/rag.md](docs/rag.md)
- Catalog curation standard: [docs/curation-standard.md](docs/curation-standard.md)
- Scheduled catalog ingestion: [docs/catalog-ingestion.md](docs/catalog-ingestion.md)
- Retrieval comparison and itinerary evaluation: [evaluation/README.md](evaluation/README.md)

The project compares text, vector, and hybrid retrieval on the same labelled
cases. Hybrid retrieval is the default because it handles both exact preference
matches and semantic similarity. The evaluation document shows the trade-offs.

## Project layout

```text
app.py                 Streamlit entry point and workspaces
src/                   Validation, retrieval, scoring, planning, LLM, and UI logic
data/activities.json   Curated activity knowledge base
evaluation/            Retrieval, itinerary, and LLM evaluation suites
tests/                 Unit and Streamlit interaction tests
docs/                  RAG, retrieval, scoring, itinerary, and curation notes
Dockerfile             Reproducible deployment image
docker-compose.yml     One-command local app environment
supabase/schema.sql    Shared persistence schema
```

## Current boundaries

TripSync is a planning prototype, not a booking tool. It does not provide live
opening hours, ticket availability, prices, routes, hotel or flight search, or
payments. Duration and pace are curated estimates. SQLite is for local use. The
shared Supabase setup has no accounts, so saved-trip history stays tied to one
browser session. A production version would need authentication, live visitor
information, and user-controlled data deletion.

## License

No license has been selected yet.
