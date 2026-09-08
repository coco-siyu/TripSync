# TripSync MVP plan

## Product outcome

TripSync helps a group with different preferences turn a fixed destination and
trip length into a practical day-by-day itinerary that the organizer can publish.

The MVP succeeds when an organizer publishes an itinerary version. A quality
guardrail is that every traveler receives at least one strong-match activity
when the catalog and schedule make that possible.

## Delivery boundary

The strict MVP is a one-week target. A second week is reserved for collaboration
enhancements and useful exports.

### Strict MVP

- Rome only.
- Two to six adult travelers.
- One to five full trip days.
- The destination and dates are already known.
- Every participant uses an email-and-password account.
- The organizer creates named, private invitation links and shares them manually.
- Travelers submit preferences independently.
- The organizer decides when enough profiles are complete to build a draft. At
  least two completed traveler profiles are required.
- TripSync creates an editable day-by-day draft using deterministic planning.
- The organizer controls edits and publishing.
- Other group members can view the current draft after refreshing the page.
- Published itinerary versions are immutable snapshots.

### Week-two scope

- Like, neutral, or dislike voting on scheduled itinerary items.
- Named traveler proposals to add, remove, replace, or move items.
- Catalog matching for free-text proposals, with clearly labeled unverified
  custom items when no match exists.
- Balanced revision suggestions using votes, profiles, fairness, pace, budget,
  and location.
- Member-submitted AI requests that run only when approved by the organizer.
- Concise PDF export.
- Optional map with activity pins.

### Later scope

- A curated dining catalog and named restaurant recommendations.
- Live opening hours, transit, route estimates, and route optimization.
- Reservation links, availability, and booking tracking.
- Partial arrival and departure days.
- Passwordless participation through secure invitation links.
- More destinations and support for at least ten travelers and ten days.
- A mobile-friendly trip mode with completion check-offs.
- Live navigation, alerts, and real-time collaboration.

## MVP experience

### 1. Create a trip

The organizer selects Rome, enters a start and end date, and names the
travelers. The dates must describe one to five consecutive full days. TripSync
uses central Rome as the default starting area; the organizer may provide an
approximate accommodation neighborhood instead of an exact address.

### 2. Collect preferences

Each traveler receives a private link and signs in before completing a concise
form.

Required fields:

- Interests, with a limit of five.
- Daily budget band.
- Walking comfort.
- Dietary needs, with an optional short note.

Optional fields:

- Up to three must-dos.
- Preferred pace: relaxed, balanced, or packed.
- Preferred time: morning, evening, or flexible.
- One short free-text note.

The organizer can see individual profiles. Other travelers see only aggregated
group insights. Sensitive notes remain organizer-only.

### 3. Build the first draft

The organizer can build whenever at least two profiles are complete. Missing
profiles are shown as pending and do not create a formal waiting rule.

The planner:

- Uses morning, afternoon, and evening blocks rather than exact times.
- Treats all trip days as full days.
- Prioritizes must-dos but does not guarantee them; omissions are explained.
- Tries to give every traveler at least one strong-match activity.
- Respects pace and daily capacity rules.
- Uses the group middle budget range as a soft planning signal.
- Shows group-level warnings for days likely to exceed that range.
- Reserves a general food allowance in the daily estimate without recommending
  a named restaurant.
- Uses central Rome or the optional accommodation neighborhood as a location
  signal, without calculating routes or travel times.

AI does not control planning decisions. The deterministic planner creates the
schedule. AI may explain a plan or propose grounded revisions only when the
organizer requests it.

### 4. Edit and share the draft

The organizer can adjust the current draft. Members have read-only access to the
latest approved draft state and see updates after refreshing the page. Real-time
synchronization is not required.

Preferences stay private according to the visibility rules above. Week-two votes
show individual choices only to the organizer; members see group totals. Named
proposals are visible to the group.

### 5. Publish a version

Publishing creates an immutable itinerary snapshot. The organizer can continue
later by starting a new editable draft from a published version.

Publishing is blocked only when:

- The schedule is structurally invalid or exceeds an unapproved hard capacity.
- A prominent safety or accessibility warning requires acknowledgment and has
  not been acknowledged.

Unresolved suggestions, soft budget warnings, and omitted must-dos do not block
publishing.

## Budget behavior

Budget is an approximate amount per person per day. It excludes flights,
accommodation, shopping, and local transport.

Travelers choose simple bands rather than entering exact amounts. TripSync uses
the group's middle band and shows estimated ranges for:

- Planned activity costs.
- One general daily food allowance covering meals and snacks.
- The combined daily estimate.

Budget is advisory. It never removes an activity or blocks publishing.
Activity ranges are derived from a documented mapping of the catalog's existing
free, low, moderate, and high price levels; they are estimates, not live prices.

## Catalog boundary

The MVP targets roughly 40 quality Rome activities, expanded from the current
catalog through automated discovery and conservative validation.

An activity publishes automatically only when its identity, location, category,
duration, planning attributes, and source pass quality checks. Incomplete records
are rejected. Ambiguous records go to the existing review queue and do not block
the release. Quality takes precedence over reaching the numeric target exactly.

## Week-two collaboration rules

- Voting happens after the first itinerary exists and never blocks publishing.
- Members vote only on scheduled items.
- Votes influence suggested revisions but never change the draft automatically.
- Likes make an item harder to replace; dislikes prompt review.
- Travelers may submit named proposals, including a short free-text place idea.
- The organizer is the only approver.
- Approved changes accumulate in an editable draft visible to all members.
- AI requests submitted by members do not run until the organizer approves them.

## PDF boundary

The week-two PDF is a concise, privacy-safe export containing:

- Days and time blocks.
- Activities and locations.
- Important warnings.
- Estimated daily budget ranges when available.

It excludes individual preferences, individual votes, proposals, alternatives,
and detailed group-fit explanations.

## Technical principles

- Keep deterministic rules authoritative for scheduling and validation.
- Treat AI output as optional, grounded, reviewable advice.
- Preserve backward compatibility for existing saved trips and itinerary versions.
- Store shared collaboration state durably; do not rely on Streamlit session state
  as the source of truth.
- Keep the preference form concise and submit it as one form to avoid unnecessary
  reruns.
- Render stable UI before slow catalog, retrieval, or AI work.
- Keep personal profile data out of member-facing payloads, not merely hidden in
  the interface.

## Release acceptance

The strict MVP is ready when:

1. A signed-in organizer can create a Rome trip and invite up to five other
   signed-in travelers for one to five dated, consecutive days.
2. At least two travelers can submit valid preference profiles independently.
3. The organizer can build a valid one-to-five-day draft before every invitee has
   replied.
4. The draft uses time blocks, includes soft budget ranges, and explains omitted
   must-dos.
5. Each traveler receives a strong match when a feasible one exists.
6. Invited members can view the current draft without seeing private profiles.
7. The organizer can publish an immutable version and create a later draft from
   it.
8. Invalid schedules and unacknowledged safety warnings cannot be published.
9. Existing deterministic tests and evaluation guardrails continue to pass.
