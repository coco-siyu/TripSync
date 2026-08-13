# Grounded RAG narration

TripSync uses retrieval-augmented generation for readable itinerary narration while
keeping activity selection and scheduling deterministic.

## Flow

1. Hybrid retrieval selects grounded activity records from the destination catalog.
2. Group-fit scoring ranks the retrieved candidates.
3. The deterministic planner builds a constraint-safe itinerary.
4. The narration prompt supplies the validated trip, the retrieved records for the
   scheduled activities, and the immutable itinerary to OpenAI.
5. The Responses API returns a Pydantic-structured `ItineraryNarrative`.
6. TripSync verifies that every narrative day and activity ID exactly matches the
   deterministic plan before accepting the response.

The LLM writes trip and day summaries, activity-fit explanations, practical notes,
and trade-offs. It does not choose activities or change the schedule.

## Knowledge-base documents and chunking

Today, one curated `Activity` record is one retrieval document. It contains a
short name, description, city, category, interests, walking level, budget,
duration, accessibility notes, and source URL. These records are already small,
self-contained, and structured, so splitting them into chunks would lose useful
connections (for example, a museum's walking level from its interests) without
improving retrieval.

The current RAG source is the curated TripSync catalog: the initial local
`data/activities.json` seed plus candidates retrieved from Wikidata and then
reviewed, corrected, and explicitly published by a human. Wikidata is a
discovery/provenance source, not unreviewed prompt context.

For semantic matching, TripSync embeds each published activity's name, category,
tags, interests, and description with OpenAI's `text-embedding-3-small` model.
The vector is stored beside the activity in Supabase and reused until that source
text changes. At trip time, the app embeds the combined group-interest query and
compares it only with activities in the requested destination. The deterministic
text retriever remains the safe fallback when semantic enrichment is unavailable.

Chunking becomes appropriate if TripSync later ingests long materials such as
official attraction pages, city guides, accessibility guides, or lengthy
traveler notes. In that case, split by meaningful sections (for example,
admission, accessibility, and visit tips), retain the activity ID/source URL on
every chunk, retrieve only the most relevant chunks, and cite their source in
the final UI. Live opening hours, prices, and availability would need a verified
live provider rather than a static chunk.

## Configuration

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Then configure:

```dotenv
OPENAI_API_KEY=replace-with-your-api-key
OPENAI_MODEL=gpt-5.6-terra
TRIPSYNC_EMBEDDING_MODEL=text-embedding-3-small
```

`OPENAI_MODEL` is optional. TripSync defaults to `gpt-5.6-terra`, selected after
the recorded live contract and held-out robustness comparisons in
[`evaluation/README.md`](../evaluation/README.md). You may override it for a
local experiment without changing the product default.

`TRIPSYNC_EMBEDDING_MODEL` is optional. The default is
`text-embedding-3-small`; it is used only for catalog/search enrichment and does
not affect itinerary narration or adjustment generation.

## Structured output

The Python SDK call uses the Responses API structured-output helper:

```python
response = client.responses.parse(
    model=model,
    input=grounded_prompt,
    text_format=ItineraryNarrative,
)
```

The output schema requires:

- one trip summary;
- the exact itinerary days in their original order;
- the exact scheduled activity IDs in their original day and order;
- a concise fit explanation for every scheduled activity;
- optional practical notes and explicit trade-offs.

The implementation follows OpenAI's
[Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and uses a model documented to support both the
[Responses API and Structured Outputs](https://developers.openai.com/api/docs/models/gpt-5.6-terra).

## Guardrails and current boundary

TripSync rejects parsed output that adds, removes, duplicates, reorders, or moves an
activity, or that changes the itinerary day sequence. The prompt also prohibits
invented prices, opening hours, live availability, route times, and booking claims.

Structural validation cannot prove that every sentence is factually perfect. The
20-case LLM contract baseline checks grounding, completeness, and adjustment
feasibility; future prompt comparisons should add human review for usefulness and
tone.

## Streamlit experience

After a deterministic itinerary is built, the itinerary screen offers **Generate
trip story**. It makes no API call until that button is pressed. On success, the
story appears above the day cards and each activity receives its grounded
explanation. Rebuilding, editing, or replacing an itinerary activity clears its
story; undo restores the story that matched the restored plan. Configuration,
quota, and API errors remain visible as a friendly message without affecting the
underlying itinerary.

## Organizer-approved adjustments

The itinerary screen also accepts a plain-language adjustment request such as
“make Day 2 calmer” or “add more food.” The LLM may return at most three
structured options. Each option can add an eligible retrieved catalog activity
to a named day, replace one activity on the same day, or remove an activity and
leave time open. The LLM cannot apply a proposal.

When the organizer chooses **Apply this suggestion**, TripSync checks the day,
activity IDs, duplicate prevention, activity count, and pace capacity again in
the deterministic planner. If an option exceeds the recommended activity or hour
limit, the organizer sees the exact consequence and must explicitly confirm
**Apply anyway**. The day remains visibly marked as over pace after that approval.
An outgoing activity is recorded as an intentional rejection, an added activity
joins the shortlist, the prior plan remains available through Undo, and the old
trip story is cleared.

## Local feedback

Every generated trip story and every adjustment option has a Helpful / Not useful
control plus an optional comment. Separately, the end of an itinerary has a
three-question overall-experience rubric: helpfulness for planning, clarity, and
fit for the group, each rated from 1 to 5, plus an optional comment. Feedback is
stored locally in the ignored `data/tripsync_feedback.db` SQLite file. Rows contain
an anonymous per-browser session ID, a hash-based generated-item or itinerary ID,
ratings, optional comment, and timestamp. They do not store the prompt, itinerary,
traveler names, or generated LLM text.

This is product feedback, not automatic model training. Review grouped comments
and ratings with a human rubric before deciding on any planner, catalog, or prompt
change.
