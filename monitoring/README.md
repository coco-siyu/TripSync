# Monitoring

TripSync stores privacy-conscious product feedback in Supabase when configured and
uses SQLite as the local fallback. Feedback records exclude prompts, itinerary
content, traveler names, and generated text. The admin dashboard aggregates these
records without exposing anonymous session identifiers.

Future operational monitoring may store:

- request and response timestamps;
- retrieval result identifiers;
- model and token-usage metadata;
- rejected activity identifiers;
- feedback reasons such as excessive cost or walking;
- regeneration outcomes.

Traveler names, health details, and other personal information should not be placed
in monitoring logs unless a clear privacy design is implemented.
