# Grounded RAG narration

TripSync uses retrieval-augmented generation for readable itinerary narration while
keeping activity selection and scheduling deterministic.

## Flow

1. Text retrieval selects grounded activity records from the destination catalog.
2. Group-fit scoring ranks the retrieved candidates.
3. The deterministic planner builds a constraint-safe itinerary.
4. The narration prompt supplies the validated trip, the retrieved records for the
   scheduled activities, and the immutable itinerary to OpenAI.
5. The Responses API returns a Pydantic-structured `ItineraryNarrative`.
6. TripSync verifies that every narrative day and activity ID exactly matches the
   deterministic plan before accepting the response.

The LLM writes trip and day summaries, activity-fit explanations, practical notes,
and trade-offs. It does not choose activities or change the schedule.

## Configuration

Create a local `.env` file from the example:

```bash
cp .env.example .env
```

Then configure:

```dotenv
OPENAI_API_KEY=replace-with-your-api-key
OPENAI_MODEL=gpt-5.6-luna
```

`OPENAI_MODEL` is optional. TripSync defaults to `gpt-5.6-luna`, the cost-sensitive
GPT-5.6 model. A later LLM evaluation will compare model and prompt configurations
before selecting the final production default.

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
[Responses API and Structured Outputs](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

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
structured options. Each option can only replace one activity on the same day
with an eligible retrieved catalog activity, or remove that activity and leave
time open. The LLM cannot apply a proposal.

When the organizer chooses **Apply this suggestion**, TripSync checks the day,
activity IDs, duplicate prevention, activity count, and pace capacity again in
the deterministic planner. The outgoing activity is recorded as an intentional
override, the replacement joins the shortlist, the prior plan remains available
through Undo, and the old trip story is cleared.

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
