# Activity retrieval

TripSync retrieves a grounded candidate set before calculating group-fit scores.
Keeping these phases separate makes it possible to evaluate whether the catalog
returned the right activities independently from how the group ranking orders them.

## Current retrieval inputs

TripSync uses a hybrid approach:

- destination city and country;
- each traveler’s normalized interests;
- each traveler’s recognized must-do activity names or stable IDs;
- searchable activity names, categories, interest tags, and descriptions.

Published catalog records are embedded once through OpenAI and stored in
Supabase. A trip query is embedded at search time and compared only with records
from the requested city and country. Text evidence and recognized must-dos remain
part of every retrieval result; hosted semantic search improves conceptual matches
such as `painting` → `art museum` without letting the system invent activities.

Capitalization and punctuation are normalized before comparison. Phrase matching
uses word boundaries, so a short interest such as `art` does not accidentally match
an unrelated word such as `party`.

## Guardrails

- Activities from another city or country are never returned.
- Duplicate activity IDs are returned once.
- Recognized must-dos always remain in the candidate set, even if a result limit
  would otherwise remove them.
- If no preference text matches but the destination exists, the destination catalog
  is returned as an explicit fallback rather than presenting an empty page.
- If the destination is absent from the catalog, the UI explains the current data
  limitation instead of attempting to score incompatible activities.

## Availability fallback

Semantic enrichment is optional. If OpenAI or the Supabase embedding table is
unavailable, TripSync continues with deterministic text matching and tells the
organizer that the fallback is active. This keeps the trip planner usable during
an outage or before the optional embedding table has been initialized.

## Ranking boundary

Text-retrieval relevance decides which activities become candidates. It does not
control the final Top 5 order. The deterministic group-fit formula in
[scoring.md](scoring.md) ranks the retrieved candidates using interests, walking
comfort, must-dos, budget, and fairness.

The UI preserves retrieval evidence on each result card under “Why this fits your
group,” so the candidate-selection and group-ranking explanations remain visible.

## Evaluation baseline

The retrieval output is deterministic and includes matched terms, traveler coverage,
must-do ownership, and reasons. The first benchmark uses eight labeled trip requests
and reports Hit Rate@K, Mean Reciprocal Rank, and Mean Recall.

At `K=5`, the current catalog and deterministic text retriever produce:

- Hit Rate@5: `1.000`;
- MRR@5: `0.938`;
- Mean Recall@5: `1.000`.

Run the benchmark with:

```bash
.venv/bin/python -m evaluation.retrieval
```

See [evaluation/README.md](../evaluation/README.md) for definitions, case-level
output, and the limitations of this small curated baseline. Vector and hybrid
retrievers should implement the same ranked-output boundary so they can be compared
against these labels without changing the metrics.
