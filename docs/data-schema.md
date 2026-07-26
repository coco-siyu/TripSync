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

## Controlled values

The first version will use controlled values where practical so retrieval results
and group-fit scoring can be evaluated consistently. Additional fields, such as
opening hours or geographic coordinates, should only be added when the application
uses them.
