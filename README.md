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
SUPABASE_PUBLISHABLE_KEY=your-browser-safe-publishable-key
TRIPSYNC_ADMIN_PASSWORD=choose-a-dashboard-password
```

The server-side key stores feedback and manages the shared activity catalog; the
browser-safe publishable key enables account sessions and RLS-protected trip
access. The password protects the Feedback insights and Curate catalog pages.
Without Supabase credentials, TripSync uses local SQLite data in `data/`.

With all three Supabase values configured, travelers can create accounts, change
their password while signed in, keep private plans across devices, and
permanently delete their account and account-linked data. A signed-in owner can
also create a seven-day viewer link from **My trips → Share trip**. A signed-in
recipient who opens it receives read-only access to that trip and its saved
itinerary versions; they cannot edit, resave, or delete the owner's data.
Signing in atomically moves plans saved in that browser into the account. Apply
`supabase/schema.sql` again when upgrading an existing deployment; it installs
the private-trip policies, guarded browser-plan transfer, and Phase 2a sharing
tables and functions. The Phase 2a migration is idempotent, so later schema
reruns preserve real sharing records.
Without account configuration, saved plans continue to use the anonymous
browser-session fallback.

In Supabase Authentication settings, enable the Email provider and set the Auth
Site URL to `https://tripsync.streamlit.app`. Add both
`http://localhost:8501/**` and `https://tripsync.streamlit.app/**` to Redirect
URLs. TripSync passes its current validated origin during signup, so local
confirmation returns to localhost while deployed confirmation returns to the
live app. Sharing uses the same local or deployed origin that is currently open,
so no additional Supabase redirect URL is required. Viewer links are capability
secrets: TripSync stores only a one-way token hash, expires links after seven
days, and removes the token from the browser URL immediately after it is
accepted. **Revoke sharing** invalidates outstanding links and removes existing
viewers. Shared editing is intentionally outside Phase 2a.

Account deletion requires a fresh password verification. Its zero-argument
database function derives the deletion target from the authenticated JWT,
atomically removes saved trips (including itinerary versions), shared-trip
memberships, and feedback,
then the server-only Supabase client removes that same Auth user. Reapply
`supabase/schema.sql` to an existing project before using this control.

Password recovery is temporarily hidden because Supabase requires custom SMTP
before its hosted email templates can be edited. Signed-in travelers can still
change their password from the Account page. Once TripSync has a verified email
domain, configure custom SMTP, set `PASSWORD_RECOVERY_ENABLED = True` in
`src/auth_ui.py`, then open **Authentication → Email Templates → Reset
Password** in Supabase and make the reset button use the one-time token hash:

```html
<h2>Reset your TripSync password</h2>
<p>Follow the link below to choose a new password.</p>
<p>
  <a href="{{ .RedirectTo }}?token_hash={{ .TokenHash }}&amp;type=recovery">
    Reset password
  </a>
</p>
<p>If you did not request this, you can safely ignore this email.</p>
```

This template lets Streamlit verify the recovery token server-side and remove
it from the address bar before showing the new-password form. Keep the two
localhost/live Redirect URLs above; no database schema change is required.

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
there across Streamlit Cloud restarts. The `catalog_destinations` view in the
same schema gives the first planning page a lightweight autocomplete index. It
derives from all existing catalog rows and automatically follows future catalog
changes, so it does not require a separate backfill. Historical candidate files in
`data/candidates/` are optional audit artifacts; they are not required for
ingestion. The curation rules are in
[docs/curation-standard.md](docs/curation-standard.md), and retrieval behavior
is in [docs/retrieval.md](docs/retrieval.md).

### Backfill route coordinates

Existing activities created before route-aware itinerary planning may not have
coordinates yet. Backfill them in place from their Wikidata source URLs; this
does not replace any curated activity details or change activity IDs. Review the
count first, then run without `--dry-run` to save the updates:

```bash
python -m src.catalog_backfill --dry-run
python -m src.catalog_backfill
```

### Backfill verified official visitor links

TripSync can also enrich every existing catalog record without reviewing places
one at a time. Wikidata's official-website property is used only to locate a
possible URL; TripSync opens the attraction's actual website, checks that the
page matches the attraction, and saves only same-organization visit, hours, and
ticket links. A Wikidata page is never presented as an official visitor site.
Unreachable or mismatched sites remain blank and are retried by later runs.

```bash
python -m src.catalog_backfill --official-sites --dry-run
python -m src.catalog_backfill --official-sites
```

Use `--refresh-verified` with `--official-sites` when the verifier changes or
when previously saved official sites should be rechecked too.

The new fields live inside the existing `activity_json` JSONB value, so this
enrichment does not require rerunning `supabase/schema.sql`.

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
DuckDB, publishes only clear, Pydantic-valid attractions to the shared catalog,
then retries missing official visitor sources across the existing catalog. A
manual run processes the entire queue.

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
payments. Duration and pace are curated estimates. SQLite is for local use.
Account-backed trips use Supabase row-level security and are private to their
owner. Invitation links, shared editing, and live visitor information remain
future production work.

## License

No license has been selected yet.
