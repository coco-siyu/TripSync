# Activity data schema

Each activity record should contain the following fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| `id` | string | Yes | Stable, unique identifier |
| `name` | string | Yes | Public activity or place name |
| `city` | string | Yes | City or destination |
| `country` | string | Yes | Country |
| `category` | string | Yes | Primary activity category |
| `interests` | list of strings | Yes | Interests served by the activity |
| `walking_level` | string | Yes | `low`, `moderate`, or `high` |
| `budget_level` | string | Yes | `free`, `low`, `moderate`, or `high` |
| `duration_hours` | number | Yes | Typical visit duration |
| `indoor` | boolean | Yes | Whether the activity is primarily indoors |
| `family_friendly` | boolean | Yes | Whether it suits a general family group |
| `accessibility_notes` | string | Yes | Mobility and accessibility considerations |
| `reservation_required` | boolean | Yes | Whether advance booking is normally required |
| `description` | string | Yes | Concise factual description |
| `source_url` | string | Yes | Source used to verify the record |
| `latitude` | number or `null` | No | WGS84 latitude used for approximate route ordering; must be paired with `longitude` |
| `longitude` | number or `null` | No | WGS84 longitude used for approximate route ordering; must be paired with `latitude` |

## Controlled values

The first version will use controlled values where practical so retrieval results
and group-fit scoring can be evaluated consistently. Geographic coordinates are
optional: when both are available, TripSync uses them to reduce itinerary
backtracking and estimate walking between stops. A record must contain both
`latitude` and `longitude`, or neither. They are estimates rather than live map,
traffic, accessibility, or opening-hours data.

Controlled values are:

- `walking_level`: `low`, `moderate`, or `high`;
- `budget_level`: `free`, `low`, `moderate`, or `high`;
- trip `pace`: `relaxed`, `balanced`, or `packed`.

## Traveler profile

| Field | Type | Required | Description |
|---|---|---:|---|
| `name` | string | Yes | Display name, unique within a trip |
| `interests` | list of strings | Yes | One to twelve normalized interest tags |
| `walking_tolerance` | string | Yes | Maximum preferred walking level |
| `food_restrictions` | list of strings | No | Dietary restrictions or allergies |
| `must_do_activities` | list of strings | No | Activities the traveler wants prioritized |

## Trip request

| Field | Type | Required | Description |
|---|---|---:|---|
| `destination` | string | Yes | Destination city or region |
| `country` | string | Yes | Destination country |
| `days` | integer | Yes | Trip length from one to five days |
| `budget_level` | string | Yes | Group budget level |
| `pace` | string | Yes | `relaxed`, `balanced`, or `packed` |
| `travelers` | list | Yes | Two to six validated traveler profiles |

All models reject unknown fields. User-entered tag lists are trimmed, converted to
lowercase, de-duplicated, and stripped of blank entries.
