# Monitoring

The first implementation will log non-sensitive development feedback locally.
Later versions may store:

- request and response timestamps;
- retrieval result identifiers;
- model and token-usage metadata;
- rejected activity identifiers;
- feedback reasons such as excessive cost or walking;
- regeneration outcomes.

Traveler names, health details, and other personal information should not be placed
in monitoring logs unless a clear privacy design is implemented.
