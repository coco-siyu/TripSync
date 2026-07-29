# Activity text retrieval

TripSync retrieves a grounded candidate set before calculating group-fit scores.
Keeping these phases separate makes it possible to evaluate whether the catalog
returned the right activities independently from how the group ranking orders them.

## Current retrieval inputs

The deterministic MVP uses:

- destination city and country;
- each traveler’s normalized interests;
- each traveler’s recognized must-do activity names or stable IDs;
- searchable activity names, categories, interest tags, and descriptions.

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

## Ranking boundary

Text-retrieval relevance decides which activities become candidates. It does not
control the final Top 5 order. The deterministic group-fit formula in
[scoring.md](scoring.md) ranks the retrieved candidates using interests, walking
comfort, must-dos, budget, and fairness.

The UI preserves retrieval evidence on each result card under “Why this fits your
group,” so the candidate-selection and group-ranking explanations remain visible.

## Next evaluation step

The retrieval output is deterministic and includes matched terms, traveler coverage,
must-do ownership, and reasons. This evidence can be used to build a small
ground-truth query set and calculate Hit Rate and Mean Reciprocal Rank before adding
vector or hybrid retrieval.
