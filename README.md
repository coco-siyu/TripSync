# TripSync

TripSync is an AI-assisted group travel planner. It turns a group's interests,
walking comfort, budget, food needs, pace, and must-dos into explainable activity
recommendations and a reviewable itinerary.

It is built as an end-to-end LLM application for the LLM Zoomcamp project: a
curated knowledge base is retrieved first, deterministic logic makes the planning
decisions, and an LLM adds grounded narration and adjustment ideas under a strict
structured-output contract.

## What it does

- Collects validated preferences for 2–6 travelers and trips of 1–5 days.
- Retrieves from a curated catalog for Rome, Florence, Milan, and Naples using
  text, vector, or hybrid retrieval.
- Scores activities for shared interests, walking and budget fit, must-dos, and
  fairness across the group.
- Shows Top 5, must-do, all, and rejected activity views; users can manage a
  shortlist before generating the itinerary.
- Builds a deterministic itinerary with daily pace, duration, transition, and
  duplicate-prevention rules.
- Lets the organizer reject, replace, or add activities. Recommendations that
  exceed the pace limit require explicit confirmation and remain visibly marked
  as over pace.
- Uses the OpenAI Responses API only for a grounded itinerary story and
  organizer-approved adjustment options; the LLM cannot silently change a plan.
- Stores local feedback and saved trips in SQLite, with a feedback-insights view
  and privacy-safe feedback export.

## How the system works

```mermaid
flowchart LR
    A["Traveler preferences"] --> B["Retrieve city catalog"]
    B --> C["Explainable group-fit scoring"]
    C --> D["Deterministic itinerary planner"]
    D --> E["Grounded LLM story / adjustment options"]
    E --> F["Organizer review and explicit approval"]
    F --> G["Saved trip and feedback"]
```

The LLM receives only the validated trip, eligible catalog records, and current
plan. Pydantic schemas plus deterministic grounding validation reject invented
activity IDs, duplicate stops, moved activities, and invalid output structures.
Read the full RAG boundary in [docs/rag.md](docs/rag.md).

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
The deterministic recommendation and itinerary flow works without making an API
call. Never commit `.env` or `.streamlit/secrets.toml`.

## Run with Docker

Build the image (the semantic retrieval model is downloaded during the build):

```bash
docker build -t tripsync .
```

Run it with an OpenAI key and a named volume for local saved trips and feedback:

```bash
docker run --rm -p 8501:8501 \
  -e OPENAI_API_KEY="your-api-key" \
  -v tripsync-state:/app/state \
  tripsync
```

Open <http://localhost:8501>. The catalog is included in the image; the mounted
`tripsync-state` volume stores only SQLite state. On a hosted platform, set
`OPENAI_API_KEY` (and optionally `OPENAI_MODEL`) through that platform's secrets
manager, never in the repository.

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

The current retrieval and itinerary baseline results are documented in
[evaluation/README.md](evaluation/README.md). The 20 contract cases and 12
held-out LLM robustness cases measure schema compliance, grounding,
completeness, and proposal applicability—not subjective travel-writing quality.

## Data and curation

`data/activities.json` is the canonical, validated activity catalog. Raw external
candidate imports remain under `data/candidates/` for review. The curation rules
are in [docs/curation-standard.md](docs/curation-standard.md), and retrieval
behavior is in [docs/retrieval.md](docs/retrieval.md).

## Project layout

```text
app.py                 Streamlit entry point and workspaces
src/                   Validation, retrieval, scoring, planning, LLM, and UI logic
data/activities.json   Curated activity knowledge base
evaluation/            Retrieval, itinerary, and LLM evaluation suites
tests/                 Unit and Streamlit interaction tests
docs/                  RAG, retrieval, scoring, itinerary, and curation notes
Dockerfile             Reproducible deployment image
```

## Current boundaries

TripSync is a planning prototype, not a booking tool. It does not provide live
opening hours, ticket availability, prices, routes, hotel or flight search, or
payments. Duration and pace values are curated planning estimates. The default
SQLite persistence is appropriate for local use or a single persistent deployment
volume; a multi-user production version should move saved trips and feedback to a
managed database.

## License

No license has been selected yet.
