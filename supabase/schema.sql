-- Phase 1 schema: private accounts and personal saved trips.
-- Run in Supabase SQL Editor. Server-side app access uses SUPABASE_SECRET_KEY.
create table if not exists public.saved_trips (
  session_id text not null,
  trip_id text not null,
  title text not null,
  trip_json jsonb not null,
  state_json jsonb not null,
  updated_at timestamptz not null,
  primary key (session_id, trip_id)
);

create table if not exists public.llm_feedback (
  session_id text not null,
  target_type text not null check (target_type in ('trip_story', 'adjustment_proposal')),
  target_id text not null,
  rating text not null check (rating in ('up', 'down')),
  comment text,
  created_at timestamptz not null,
  primary key (session_id, target_type, target_id)
);

create table if not exists public.overall_experience_feedback (
  session_id text not null,
  itinerary_id text not null,
  helpfulness integer not null check (helpfulness between 1 and 5),
  clarity integer not null check (clarity between 1 and 5),
  group_fit integer not null check (group_fit between 1 and 5),
  comment text,
  created_at timestamptz not null,
  primary key (session_id, itinerary_id)
);

-- Shared, Pydantic-validated activity catalog. `activity_json` contains the
-- complete Activity contract, including optional latitude/longitude route
-- coordinates. This avoids a SQL migration whenever the validated document
-- gains an optional planning field. It is edited only through the
-- admin-protected Curate catalog workspace.
create table if not exists public.catalog_activities (
  activity_id text primary key,
  activity_json jsonb not null,
  updated_at timestamptz not null default now()
);

-- Lightweight autocomplete index derived from the live catalog. Because this
-- is a view, activities that already exist appear immediately when the schema
-- is applied, and future catalog inserts, edits, and deletes stay synchronized
-- without a separate backfill or ingestion step.
create or replace view public.catalog_destinations as
select distinct
  btrim(activity_json ->> 'city') as city,
  btrim(activity_json ->> 'country') as country
from public.catalog_activities
where nullif(btrim(activity_json ->> 'city'), '') is not null
  and nullif(btrim(activity_json ->> 'country'), '') is not null;

-- Optional hosted semantic-search enrichment. The app stores an embedding for
-- each published activity and compares only records from the trip's destination.
-- JSONB avoids a heavy local ML runtime and does not require pgvector for this
-- small, city-scoped catalog.
create table if not exists public.catalog_activity_embeddings (
  activity_id text primary key references public.catalog_activities(activity_id) on delete cascade,
  embedding_model text not null,
  content_hash text not null,
  embedding jsonb not null,
  updated_at timestamptz not null default now()
);

-- Ambiguous source records are kept separate from the live catalog. They are
-- never used in traveller recommendations until an admin explicitly approves
-- and Pydantic-validates them.
create table if not exists public.catalog_review_candidates (
  review_id text primary key,
  city text not null,
  country text not null,
  candidate_json jsonb not null,
  reason text not null,
  confidence numeric not null,
  updated_at timestamptz not null default now()
);

create index if not exists catalog_review_candidates_location_idx
  on public.catalog_review_candidates (country, city);

-- One durable cursor lets the scheduled catalog workflow continue from the
-- next destination instead of recalculating a position from the calendar.
create table if not exists public.catalog_ingestion_state (
  cursor_id text primary key,
  next_destination_key text not null,
  updated_at timestamptz not null default now()
);

-- A small, human-readable audit trail for scheduled and manual ingestion.
create table if not exists public.catalog_ingestion_runs (
  id bigint generated always as identity primary key,
  run_id text not null,
  destination_key text not null,
  city text not null,
  country text not null,
  status text not null check (status in ('succeeded', 'failed')),
  published_count integer,
  error_message text,
  completed_at timestamptz not null
);

create index if not exists catalog_ingestion_runs_completed_at_idx
  on public.catalog_ingestion_runs (completed_at desc);

alter table public.saved_trips enable row level security;
alter table public.llm_feedback enable row level security;
alter table public.overall_experience_feedback enable row level security;
alter table public.catalog_activities enable row level security;
alter table public.catalog_activity_embeddings enable row level security;
alter table public.catalog_review_candidates enable row level security;
alter table public.catalog_ingestion_state enable row level security;
alter table public.catalog_ingestion_runs enable row level security;

-- Personal trip access uses the signed-in user's JWT. Administrative catalog
-- and aggregate-feedback jobs continue to use the server-only service key.
create schema if not exists private;
grant usage on schema private to authenticated;

revoke all on table public.saved_trips from anon, authenticated;
grant select, insert, delete on table public.saved_trips to authenticated;
grant update (title, trip_json, state_json, updated_at)
  on table public.saved_trips to authenticated;

drop policy if exists "Signed-in users can read accessible trips"
  on public.saved_trips;
drop policy if exists "Signed-in users can read their trips"
  on public.saved_trips;
create policy "Signed-in users can read their trips"
on public.saved_trips for select
to authenticated
using (
  (select auth.uid()) is not null
  and session_id = (select auth.uid())::text
);

drop policy if exists "Signed-in users can create owned trips"
  on public.saved_trips;
create policy "Signed-in users can create owned trips"
on public.saved_trips for insert
to authenticated
with check (
  (select auth.uid()) is not null
  and session_id = (select auth.uid())::text
);

drop policy if exists "Collaborators can update accessible trips"
  on public.saved_trips;
drop policy if exists "Signed-in users can update their trips"
  on public.saved_trips;
create policy "Signed-in users can update their trips"
on public.saved_trips for update
to authenticated
using (
  (select auth.uid()) is not null
  and session_id = (select auth.uid())::text
)
with check (
  (select auth.uid()) is not null
  and session_id = (select auth.uid())::text
);

drop policy if exists "Owners can delete their trips"
  on public.saved_trips;
create policy "Owners can delete their trips"
on public.saved_trips for delete
to authenticated
using (
  (select auth.uid()) is not null
  and session_id = (select auth.uid())::text
);

-- The anonymous browser ID is a 128-bit capability generated by TripSync. The
-- strict 32-character format prevents this function from accepting another
-- account's UUID as a source namespace. Row locks make the move atomic even if
-- the same browser session submits two requests at once.
create or replace function private.claim_anonymous_trips(
  anonymous_session_id text
)
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  source_trip_id text;
  target_trip_id text;
  claimed_count integer := 0;
begin
  if account_user_id is null then
    raise exception 'Sign in before moving browser trips.' using errcode = '42501';
  end if;

  if anonymous_session_id is null
    or anonymous_session_id !~ '^[a-f0-9]{32}$' then
    raise exception 'Anonymous browser session is invalid.' using errcode = '22023';
  end if;

  for source_trip_id in
    select saved_trips.trip_id
    from public.saved_trips
    where saved_trips.session_id = anonymous_session_id
    for update
  loop
    target_trip_id := source_trip_id;
    if exists (
      select 1
      from public.saved_trips
      where saved_trips.session_id = account_user_id::text
        and saved_trips.trip_id = target_trip_id
    ) then
      target_trip_id := pg_catalog.replace(
        pg_catalog.gen_random_uuid()::text,
        '-',
        ''
      );
    end if;

    update public.saved_trips
    set session_id = account_user_id::text,
        trip_id = target_trip_id
    where session_id = anonymous_session_id
      and trip_id = source_trip_id;
    claimed_count := claimed_count + 1;
  end loop;

  return claimed_count;
end;
$$;

revoke all on function private.claim_anonymous_trips(text) from public;
grant execute on function private.claim_anonymous_trips(text) to authenticated;

-- Keep the API-visible wrapper as security invoker. The privileged operation
-- lives in the unexposed private schema with a pinned search path.
create or replace function public.claim_anonymous_trips(
  anonymous_session_id text
)
returns integer
language sql
security invoker
set search_path = ''
as $$
  select private.claim_anonymous_trips(anonymous_session_id);
$$;

revoke all on function public.claim_anonymous_trips(text) from public;
grant execute on function public.claim_anonymous_trips(text) to authenticated;

-- Account deletion never accepts a target ID. The fresh authenticated JWT is
-- the sole source of identity, and all account-linked records are removed in
-- the same database transaction before the server deletes the Auth user.
create or replace function private.delete_my_account_data()
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  deleted_trips integer := 0;
  deleted_llm_feedback integer := 0;
  deleted_overall_feedback integer := 0;
begin
  if account_user_id is null then
    raise exception 'Sign in before deleting account data.' using errcode = '42501';
  end if;

  delete from public.saved_trips
  where session_id = account_user_id::text;
  get diagnostics deleted_trips = row_count;

  delete from public.llm_feedback
  where session_id = account_user_id::text;
  get diagnostics deleted_llm_feedback = row_count;

  delete from public.overall_experience_feedback
  where session_id = account_user_id::text;
  get diagnostics deleted_overall_feedback = row_count;

  return pg_catalog.jsonb_build_object(
    'saved_trips', deleted_trips,
    'llm_feedback', deleted_llm_feedback,
    'overall_experience_feedback', deleted_overall_feedback
  );
end;
$$;

revoke all on function private.delete_my_account_data() from public;
grant execute on function private.delete_my_account_data() to authenticated;

create or replace function public.delete_my_account_data()
returns jsonb
language sql
security invoker
set search_path = ''
as $$
  select private.delete_my_account_data();
$$;

revoke all on function public.delete_my_account_data() from public;
grant execute on function public.delete_my_account_data() to authenticated;

-- If a collaboration preview schema was applied earlier, make it inert without
-- deleting its data. Phase 2 can replace these objects after it has a globally
-- unique trip identity and a two-user RLS integration test.
drop function if exists public.claim_trip_invitation(text);
drop function if exists private.claim_trip_invitation(text);
drop function if exists private.is_trip_member(text);
drop function if exists private.is_trip_member(text, uuid);

do $$
begin
  if pg_catalog.to_regclass('public.trip_members') is not null then
    execute 'revoke all on table public.trip_members from anon, authenticated';
    execute 'drop policy if exists "Travelers can read their memberships" on public.trip_members';
    execute 'drop policy if exists "Travelers can leave shared trips" on public.trip_members';
  end if;
  if pg_catalog.to_regclass('public.trip_invitations') is not null then
    execute 'revoke all on table public.trip_invitations from anon, authenticated';
    execute 'drop policy if exists "Owners can read their invitations" on public.trip_invitations';
    execute 'drop policy if exists "Owners can create invitations" on public.trip_invitations';
    execute 'drop policy if exists "Owners can revoke invitations" on public.trip_invitations';
  end if;
end;
$$;
