# Cross-city curation standard

TripSync uses one activity standard for every city and country. A city's
attractions may differ, but its records, retrieval rules, scoring rules, and
evaluation measurements do not.

## Required activity contract

Every approved activity must pass the shared `Activity` Pydantic model:

- stable ID, name, city, country, category, and source URL;
- one or more normalized interest tags;
- walking level, budget level, typical duration, and indoor status;
- family suitability, accessibility notes, reservation expectation, and a
  factual description.

Use the common tag vocabulary whenever applicable: `ancient rome`,
`archaeology`, `architecture`, `art`, `culture`, `cycling`, `design`, `food`,
`history`, `industrial design`, `local culture`, `music`, `nature`, `outdoor`,
`photography`, `relaxation`, `religion`, `sculpture`, and `shopping`.

## Common curation checks

Before promotion, verify that the place is visitor-facing, its source link is
grounded, its tags describe what a traveler experiences, and its duration,
walking, budget, and booking fields are plausible. Do not use travel
infrastructure as an activity. Institutions such as universities and libraries
need a clear visitor experience.

## Common evaluation contract

Every retrieval case has the same Pydantic `TripRequest` plus a list of
acceptable activity IDs. Hit Rate@K, MRR@K, and recall are calculated identically
for Rome, Florence, Milan, Venice, or future destinations. Only the trip and
location-grounded expected IDs change.
