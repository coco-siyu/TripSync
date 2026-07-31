# Catalog sources and attribution

## Wikidata candidate discovery

TripSync uses the Wikidata Query Service to discover attraction candidates for
Rome, Florence, Milan, and Venice. Each generated candidate file records the
city query context, Wikidata entity IDs, source links, and the date it was
generated.

Wikidata structured data is available under CC0. Candidate files therefore
include the attribution: “Data sourced from Wikidata.” See the
[Wikidata Query Service help](https://www.wikidata.org/wiki/Help:SPARQL) and
[CC0 licence](https://creativecommons.org/publicdomain/zero/1.0/).

## Review boundary

Wikidata candidates are **not** TripSync activities. Before a candidate is
promoted to `data/activities.json`, a reviewer must verify and provide every
required activity field, especially:

- a useful category and interests;
- duration, walking level, and budget level;
- indoor/family suitability and accessibility notes;
- whether advance reservations are normally required; and
- a factual short description and an appropriate verification URL.

This prevents the importer from guessing planning constraints that affect group
fit and itinerary feasibility.

## Future source: OpenStreetMap

OpenStreetMap data may later add outdoor places, viewpoints, neighbourhoods,
and parks through the Overpass API. Those records will retain their OpenStreetMap
identity and include the required OpenStreetMap attribution before use.
