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
  deleted_shared_trips integer := 0;
  deleted_preference_drafts integer := 0;
  cleared_preference_profiles integer := 0;
  deleted_trips integer := 0;
  deleted_llm_feedback integer := 0;
  deleted_overall_feedback integer := 0;
begin
  if account_user_id is null then
    raise exception 'Sign in before deleting account data.' using errcode = '42501';
  end if;

  delete from public.trip_members
  where member_id = account_user_id;
  get diagnostics deleted_shared_trips = row_count;

  update public.preference_slots
  set member_id = null, profile_json = null, updated_at = pg_catalog.now()
  where member_id = account_user_id
    and not exists (
      select 1 from public.preference_drafts
      where preference_drafts.draft_id = preference_slots.draft_id
        and preference_drafts.owner_id = account_user_id
    );
  get diagnostics cleared_preference_profiles = row_count;

  delete from public.preference_drafts
  where owner_id = account_user_id;
  get diagnostics deleted_preference_drafts = row_count;

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
    'shared_trips', deleted_shared_trips,
    'preference_drafts', deleted_preference_drafts,
    'preference_profiles', cleared_preference_profiles,
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

-- Phase 2 sharing: viewer links remain read-only, while an explicitly selected
-- collaborator role may append itinerary versions through a guarded RPC. Trip
-- ownership, brief editing, sharing controls, and deletion remain owner-only.
-- Invitation records contain only a one-way token hash; the link capability is
-- shown to its creator once and is never recoverable from the database.
drop function if exists public.claim_trip_invitation(text);
drop function if exists private.claim_trip_invitation(text);
drop function if exists private.is_trip_member(text);
drop function if exists private.is_trip_member(text, uuid);
drop function if exists public.revoke_trip_sharing(text);
drop function if exists private.revoke_trip_sharing(text);
drop function if exists public.append_shared_itinerary_version(text, text, jsonb);
drop function if exists private.append_shared_itinerary_version(text, text, jsonb);

do $$
begin
  if pg_catalog.to_regclass('public.trip_members') is not null
    and exists (
      select 1
      from (values
          ('owner_id', 'text'),
          ('trip_id', 'text'),
          ('member_id', 'uuid'),
          ('role', 'text'),
          ('joined_at', 'timestamp with time zone')
      ) as required(column_name, data_type)
      where not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'trip_members'
          and columns.column_name = required.column_name
          and columns.data_type = required.data_type
      )
    ) then
    execute 'drop table public.trip_members cascade';
  end if;
  if pg_catalog.to_regclass('public.trip_invitations') is not null
    and exists (
      select 1
      from (values
          ('token_hash', 'text'),
          ('owner_id', 'text'),
          ('trip_id', 'text'),
          ('expires_at', 'timestamp with time zone'),
          ('created_at', 'timestamp with time zone')
      ) as required(column_name, data_type)
      where not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'trip_invitations'
          and columns.column_name = required.column_name
          and columns.data_type = required.data_type
      )
    ) then
    execute 'drop table public.trip_invitations cascade';
  end if;
end;
$$;

create table if not exists public.trip_members (
  owner_id text not null,
  trip_id text not null,
  member_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'viewer',
  joined_at timestamptz not null default now(),
  primary key (owner_id, trip_id, member_id),
  foreign key (owner_id, trip_id)
    references public.saved_trips(session_id, trip_id) on delete cascade,
  check (owner_id <> member_id::text)
);

create table if not exists public.trip_invitations (
  token_hash text primary key check (token_hash ~ '^[a-f0-9]{64}$'),
  owner_id text not null,
  trip_id text not null,
  role text not null default 'viewer',
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (owner_id, trip_id)
    references public.saved_trips(session_id, trip_id) on delete cascade
);

alter table public.trip_members
  drop constraint if exists trip_members_role_check;
alter table public.trip_members
  add constraint trip_members_role_check
  check (role in ('viewer', 'collaborator'));
alter table public.trip_invitations
  add column if not exists role text not null default 'viewer';
alter table public.trip_invitations
  drop constraint if exists trip_invitations_role_check;
alter table public.trip_invitations
  add constraint trip_invitations_role_check
  check (role in ('viewer', 'collaborator'));

create index if not exists trip_members_member_idx
  on public.trip_members (member_id, joined_at desc);
create index if not exists trip_invitations_owner_trip_idx
  on public.trip_invitations (owner_id, trip_id);
create index if not exists trip_invitations_expires_idx
  on public.trip_invitations (expires_at);

alter table public.trip_members enable row level security;
alter table public.trip_invitations enable row level security;

revoke all on table public.trip_members from anon, authenticated;
grant select, delete on table public.trip_members to authenticated;

drop policy if exists "Owners and viewers can read memberships"
  on public.trip_members;
drop policy if exists "Owners and members can read memberships"
  on public.trip_members;
create policy "Owners and members can read memberships"
on public.trip_members for select
to authenticated
using (
  member_id = (select auth.uid())
  or owner_id = (select auth.uid())::text
);

drop policy if exists "Owners can revoke and viewers can leave"
  on public.trip_members;
drop policy if exists "Owners can revoke and members can leave"
  on public.trip_members;
create policy "Owners can revoke and members can leave"
on public.trip_members for delete
to authenticated
using (
  member_id = (select auth.uid())
  or owner_id = (select auth.uid())::text
);

revoke all on table public.trip_invitations from anon, authenticated;
grant select, insert, delete on table public.trip_invitations to authenticated;

drop policy if exists "Owners can read their invitations"
  on public.trip_invitations;
create policy "Owners can read their invitations"
on public.trip_invitations for select
to authenticated
using (owner_id = (select auth.uid())::text);

drop policy if exists "Owners can create invitations"
  on public.trip_invitations;
create policy "Owners can create invitations"
on public.trip_invitations for insert
to authenticated
with check (
  owner_id = (select auth.uid())::text
  and exists (
    select 1
    from public.saved_trips
    where saved_trips.session_id = owner_id
      and saved_trips.trip_id = trip_invitations.trip_id
  )
);

drop policy if exists "Owners can revoke invitations"
  on public.trip_invitations;
create policy "Owners can revoke invitations"
on public.trip_invitations for delete
to authenticated
using (owner_id = (select auth.uid())::text);

-- Shared members may read saved snapshots, while direct mutation policies stay
-- owner-only. Collaborator writes use only the append RPC below.
drop policy if exists "Signed-in users can read their trips"
  on public.saved_trips;
drop policy if exists "Signed-in users can read accessible trips"
  on public.saved_trips;
create policy "Signed-in users can read accessible trips"
on public.saved_trips for select
to authenticated
using (
  session_id = (select auth.uid())::text
  or exists (
    select 1
    from public.trip_members
    where trip_members.owner_id = saved_trips.session_id
      and trip_members.trip_id = saved_trips.trip_id
      and trip_members.member_id = (select auth.uid())
      and trip_members.role in ('viewer', 'collaborator')
  )
);

create extension if not exists pgcrypto with schema extensions;

create or replace function private.claim_trip_invitation(
  invite_token text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  invitation public.trip_invitations%rowtype;
  invite_hash text;
begin
  if account_user_id is null then
    raise exception 'Sign in before accepting a trip invitation.' using errcode = '42501';
  end if;
  if invite_token is null or pg_catalog.length(invite_token) < 32
    or pg_catalog.length(invite_token) > 256 then
    raise exception 'Invitation link is invalid.' using errcode = '22023';
  end if;

  invite_hash := pg_catalog.encode(
    extensions.digest(invite_token, 'sha256'),
    'hex'
  );
  select * into invitation
  from public.trip_invitations
  where token_hash = invite_hash
  for update;

  if not found or invitation.expires_at <= pg_catalog.now() then
    raise exception 'Invitation link is invalid or expired.' using errcode = '22023';
  end if;

  if invitation.owner_id <> account_user_id::text then
    insert into public.trip_members (owner_id, trip_id, member_id, role)
    values (
      invitation.owner_id,
      invitation.trip_id,
      account_user_id,
      invitation.role
    )
    on conflict (owner_id, trip_id, member_id) do update
    set role = excluded.role;
  end if;

  return pg_catalog.jsonb_build_object(
    'owner_id', invitation.owner_id,
    'trip_id', invitation.trip_id,
    'role', invitation.role
  );
end;
$$;

revoke all on function private.claim_trip_invitation(text) from public;
grant execute on function private.claim_trip_invitation(text) to authenticated;

create or replace function public.claim_trip_invitation(
  invite_token text
)
returns jsonb
language sql
security invoker
set search_path = ''
as $$
  select private.claim_trip_invitation(invite_token);
$$;

revoke all on function public.claim_trip_invitation(text) from public;
grant execute on function public.claim_trip_invitation(text) to authenticated;

-- Collaborators can append a validated itinerary-shaped snapshot, but cannot
-- replace existing versions or mutate the owner's trip brief. A row lock keeps
-- simultaneous collaborator saves from overwriting each other.
create or replace function private.append_shared_itinerary_version(
  target_owner_id text,
  target_trip_id text,
  itinerary_version jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  current_state jsonb;
  current_trip jsonb;
  current_versions jsonb;
  sanitized_version jsonb;
  version_label text;
begin
  if account_user_id is null then
    raise exception 'Sign in before saving an itinerary.' using errcode = '42501';
  end if;
  if target_owner_id is null or target_trip_id is null
    or not exists (
      select 1 from public.trip_members
      where owner_id = target_owner_id
        and trip_id = target_trip_id
        and member_id = account_user_id
        and role = 'collaborator'
    ) then
    raise exception 'Collaborator access is required.' using errcode = '42501';
  end if;
  if itinerary_version is null
    or pg_catalog.jsonb_typeof(itinerary_version) <> 'object'
    or pg_catalog.jsonb_typeof(itinerary_version -> 'itinerary_plan') <> 'object'
    or (
      itinerary_version ? 'selected_activity_ids'
      and pg_catalog.jsonb_typeof(itinerary_version -> 'selected_activity_ids')
        <> 'array'
    )
    or (
      itinerary_version ? 'dismissed_must_do_ids'
      and pg_catalog.jsonb_typeof(itinerary_version -> 'dismissed_must_do_ids')
        <> 'array'
    )
    or (
      itinerary_version ? 'auto_select_must_dos'
      and pg_catalog.jsonb_typeof(itinerary_version -> 'auto_select_must_dos')
        <> 'boolean'
    )
    or (
      itinerary_version ? 'rejected_activities'
      and pg_catalog.jsonb_typeof(itinerary_version -> 'rejected_activities')
        <> 'object'
    )
    or pg_catalog.octet_length(itinerary_version::text) > 524288 then
    raise exception 'Itinerary version is invalid.' using errcode = '22023';
  end if;

  select state_json, trip_json into current_state, current_trip
  from public.saved_trips
  where session_id = target_owner_id and trip_id = target_trip_id
  for update;
  if not found then
    raise exception 'Shared trip was not found.' using errcode = '22023';
  end if;
  if coalesce(
      pg_catalog.btrim(itinerary_version #>> '{itinerary_plan,destination}'),
      ''
    ) <> pg_catalog.btrim(current_trip ->> 'destination')
    or coalesce(
      pg_catalog.btrim(itinerary_version #>> '{itinerary_plan,country}'),
      ''
    ) <> pg_catalog.btrim(current_trip ->> 'country')
    or pg_catalog.jsonb_typeof(itinerary_version #> '{itinerary_plan,days}')
      <> 'array' then
    raise exception 'Itinerary does not match this trip.' using errcode = '22023';
  end if;

  current_versions := case
    when pg_catalog.jsonb_typeof(current_state -> 'itinerary_versions') = 'array'
      then current_state -> 'itinerary_versions'
    else '[]'::jsonb
  end;
  version_label := pg_catalog.left(
    pg_catalog.btrim(coalesce(itinerary_version ->> 'label', '')),
    80
  );
  if version_label = '' then
    version_label := 'Itinerary ' || (pg_catalog.jsonb_array_length(current_versions) + 1);
  end if;

  sanitized_version := pg_catalog.jsonb_build_object(
    'version_id', pg_catalog.replace(pg_catalog.gen_random_uuid()::text, '-', ''),
    'label', version_label,
    'saved_at', pg_catalog.now(),
    'created_by', account_user_id::text,
    'selected_activity_ids', coalesce(
      itinerary_version -> 'selected_activity_ids', '[]'::jsonb
    ),
    'dismissed_must_do_ids', coalesce(
      itinerary_version -> 'dismissed_must_do_ids', '[]'::jsonb
    ),
    'auto_select_must_dos', coalesce(
      itinerary_version -> 'auto_select_must_dos', 'true'::jsonb
    ),
    'itinerary_plan', itinerary_version -> 'itinerary_plan',
    'rejected_activities', coalesce(
      itinerary_version -> 'rejected_activities', '{}'::jsonb
    ),
    'itinerary_narrative', coalesce(
      itinerary_version -> 'itinerary_narrative', 'null'::jsonb
    )
  );

  update public.saved_trips
  set state_json = current_state || pg_catalog.jsonb_build_object(
        'itinerary_versions', current_versions || pg_catalog.jsonb_build_array(sanitized_version),
        'active_itinerary_version_id', sanitized_version ->> 'version_id'
      ),
      updated_at = pg_catalog.now()
  where session_id = target_owner_id and trip_id = target_trip_id;

  return sanitized_version;
end;
$$;

revoke all on function private.append_shared_itinerary_version(text, text, jsonb)
  from public;
grant execute on function private.append_shared_itinerary_version(text, text, jsonb)
  to authenticated;

create or replace function public.append_shared_itinerary_version(
  target_owner_id text,
  target_trip_id text,
  itinerary_version jsonb
)
returns jsonb
language sql
security invoker
set search_path = ''
as $$
  select private.append_shared_itinerary_version(
    target_owner_id,
    target_trip_id,
    itinerary_version
  );
$$;

revoke all on function public.append_shared_itinerary_version(text, text, jsonb)
  from public;
grant execute on function public.append_shared_itinerary_version(text, text, jsonb)
  to authenticated;

create or replace function private.revoke_trip_sharing(
  target_trip_id text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  revoked_links integer := 0;
  removed_viewers integer := 0;
begin
  if account_user_id is null then
    raise exception 'Sign in before changing trip sharing.' using errcode = '42501';
  end if;
  if target_trip_id is null or not exists (
    select 1 from public.saved_trips
    where session_id = account_user_id::text
      and trip_id = target_trip_id
  ) then
    raise exception 'Owned trip was not found.' using errcode = '42501';
  end if;

  delete from public.trip_invitations
  where owner_id = account_user_id::text and trip_id = target_trip_id;
  get diagnostics revoked_links = row_count;

  delete from public.trip_members
  where owner_id = account_user_id::text and trip_id = target_trip_id;
  get diagnostics removed_viewers = row_count;

  return pg_catalog.jsonb_build_object(
    'revoked_links', revoked_links,
    'removed_viewers', removed_viewers
  );
end;
$$;

revoke all on function private.revoke_trip_sharing(text) from public;
grant execute on function private.revoke_trip_sharing(text) to authenticated;

create or replace function public.revoke_trip_sharing(
  target_trip_id text
)
returns jsonb
language sql
security invoker
set search_path = ''
as $$
  select private.revoke_trip_sharing(target_trip_id);
$$;

revoke all on function public.revoke_trip_sharing(text) from public;
grant execute on function public.revoke_trip_sharing(text) to authenticated;

-- Phase 2.5 group setup: collect one named preference profile per traveler
-- before recommendations exist. These capabilities are deliberately separate
-- from saved-trip viewer/collaborator access.
create table if not exists public.preference_drafts (
  draft_id text primary key check (draft_id ~ '^[a-f0-9]{32}$'),
  owner_id uuid not null references auth.users(id) on delete cascade,
  title text not null check (char_length(btrim(title)) between 1 and 160),
  trip_json jsonb not null check (jsonb_typeof(trip_json) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.preference_slots (
  draft_id text not null references public.preference_drafts(draft_id) on delete cascade,
  slot_id text not null check (slot_id ~ '^[a-f0-9]{32}$'),
  traveler_name text not null check (
    char_length(btrim(traveler_name)) between 1 and 80
  ),
  position smallint not null check (position between 0 and 5),
  member_id uuid references auth.users(id) on delete set null,
  profile_json jsonb check (
    profile_json is null or jsonb_typeof(profile_json) = 'object'
  ),
  updated_at timestamptz not null default now(),
  primary key (draft_id, slot_id),
  unique (draft_id, position)
);

create unique index if not exists preference_slots_unique_name_idx
  on public.preference_slots (draft_id, lower(btrim(traveler_name)));
create index if not exists preference_slots_member_idx
  on public.preference_slots (member_id, updated_at desc);
create unique index if not exists preference_slots_one_member_per_draft_idx
  on public.preference_slots (draft_id, member_id)
  where member_id is not null;

create table if not exists public.preference_invitations (
  token_hash text primary key check (token_hash ~ '^[a-f0-9]{64}$'),
  draft_id text not null,
  slot_id text not null,
  owner_id uuid not null references auth.users(id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  foreign key (draft_id, slot_id)
    references public.preference_slots(draft_id, slot_id) on delete cascade
);

create index if not exists preference_invitations_slot_idx
  on public.preference_invitations (draft_id, slot_id);
create index if not exists preference_invitations_expires_idx
  on public.preference_invitations (expires_at);

alter table public.preference_drafts enable row level security;
alter table public.preference_slots enable row level security;
alter table public.preference_invitations enable row level security;

revoke all on table public.preference_drafts from anon, authenticated;
grant select, delete on table public.preference_drafts to authenticated;
revoke all on table public.preference_slots from anon, authenticated;
grant select on table public.preference_slots to authenticated;
revoke all on table public.preference_invitations from anon, authenticated;
grant select, delete on table public.preference_invitations to authenticated;

drop policy if exists "Owners can read preference drafts"
  on public.preference_drafts;
create policy "Owners can read preference drafts"
on public.preference_drafts for select
to authenticated
using (owner_id = (select auth.uid()));

drop policy if exists "Owners can delete preference drafts"
  on public.preference_drafts;
create policy "Owners can delete preference drafts"
on public.preference_drafts for delete
to authenticated
using (owner_id = (select auth.uid()));

drop policy if exists "Owners and assigned travelers can read preference slots"
  on public.preference_slots;
create policy "Owners and assigned travelers can read preference slots"
on public.preference_slots for select
to authenticated
using (
  member_id = (select auth.uid())
  or exists (
    select 1 from public.preference_drafts
    where preference_drafts.draft_id = preference_slots.draft_id
      and preference_drafts.owner_id = (select auth.uid())
  )
);

drop policy if exists "Owners can manage preference invitations"
  on public.preference_invitations;
create policy "Owners can manage preference invitations"
on public.preference_invitations for select
to authenticated
using (owner_id = (select auth.uid()));

drop policy if exists "Owners can delete preference invitations"
  on public.preference_invitations;
create policy "Owners can delete preference invitations"
on public.preference_invitations for delete
to authenticated
using (owner_id = (select auth.uid()));

create or replace function private.create_preference_draft(
  target_draft_id text,
  draft_title text,
  trip_payload jsonb,
  slot_payloads jsonb
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  slot jsonb;
  slot_count integer;
begin
  if account_user_id is null then
    raise exception 'Sign in before inviting travelers.' using errcode = '42501';
  end if;
  if target_draft_id is null or target_draft_id !~ '^[a-f0-9]{32}$'
    or draft_title is null
    or char_length(pg_catalog.btrim(draft_title)) not between 1 and 160
    or trip_payload is null or pg_catalog.jsonb_typeof(trip_payload) <> 'object'
    or pg_catalog.octet_length(trip_payload::text) > 16384
    or slot_payloads is null or pg_catalog.jsonb_typeof(slot_payloads) <> 'array' then
    raise exception 'Preference draft is invalid.' using errcode = '22023';
  end if;
  slot_count := pg_catalog.jsonb_array_length(slot_payloads);
  if slot_count not between 2 and 6
    or (select count(distinct pg_catalog.lower(pg_catalog.btrim(value ->> 'traveler_name')))
        from pg_catalog.jsonb_array_elements(slot_payloads)) <> slot_count then
    raise exception 'Traveler slots are invalid.' using errcode = '22023';
  end if;

  insert into public.preference_drafts (
    draft_id, owner_id, title, trip_json
  ) values (
    target_draft_id, account_user_id, pg_catalog.btrim(draft_title), trip_payload
  );

  for slot in select value from pg_catalog.jsonb_array_elements(slot_payloads)
  loop
    if coalesce(slot ->> 'slot_id', '') !~ '^[a-f0-9]{32}$'
      or char_length(pg_catalog.btrim(coalesce(slot ->> 'traveler_name', '')))
        not between 1 and 80
      or (slot ->> 'position')::integer not between 0 and 5
      or (
        (slot ->> 'position')::integer = 0
        and pg_catalog.jsonb_typeof(slot -> 'profile') <> 'object'
      )
      or (
        (slot ->> 'position')::integer > 0
        and (
          coalesce(slot ->> 'token_hash', '') !~ '^[a-f0-9]{64}$'
          or (slot ->> 'expires_at')::timestamptz <= pg_catalog.now()
        )
      ) then
      raise exception 'Traveler slot is invalid.' using errcode = '22023';
    end if;

    insert into public.preference_slots (
      draft_id, slot_id, traveler_name, position, member_id, profile_json
    ) values (
      target_draft_id,
      slot ->> 'slot_id',
      pg_catalog.btrim(slot ->> 'traveler_name'),
      (slot ->> 'position')::smallint,
      case when (slot ->> 'position')::integer = 0 then account_user_id else null end,
      case when (slot ->> 'position')::integer = 0 then slot -> 'profile' else null end
    );

    if (slot ->> 'position')::integer > 0 then
      insert into public.preference_invitations (
        token_hash, draft_id, slot_id, owner_id, expires_at
      ) values (
        slot ->> 'token_hash',
        target_draft_id,
        slot ->> 'slot_id',
        account_user_id,
        (slot ->> 'expires_at')::timestamptz
      );
    end if;
  end loop;

  return target_draft_id;
end;
$$;

revoke all on function private.create_preference_draft(text, text, jsonb, jsonb)
  from public;
grant execute on function private.create_preference_draft(text, text, jsonb, jsonb)
  to authenticated;

create or replace function public.create_preference_draft(
  target_draft_id text,
  draft_title text,
  trip_payload jsonb,
  slot_payloads jsonb
)
returns text
language sql
security invoker
set search_path = ''
as $$
  select private.create_preference_draft(
    target_draft_id, draft_title, trip_payload, slot_payloads
  );
$$;

revoke all on function public.create_preference_draft(text, text, jsonb, jsonb)
  from public;
grant execute on function public.create_preference_draft(text, text, jsonb, jsonb)
  to authenticated;

create or replace function private.replace_preference_invitation(
  target_draft_id text,
  target_slot_id text,
  new_token_hash text,
  new_expires_at timestamptz
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
begin
  if account_user_id is null
    or new_token_hash !~ '^[a-f0-9]{64}$'
    or new_expires_at <= pg_catalog.now()
    or not exists (
      select 1
      from public.preference_slots
      join public.preference_drafts using (draft_id)
      where preference_slots.draft_id = target_draft_id
        and preference_slots.slot_id = target_slot_id
        and preference_slots.member_id is null
        and preference_slots.profile_json is null
        and preference_drafts.owner_id = account_user_id
    ) then
    raise exception 'Pending traveler slot was not found.' using errcode = '42501';
  end if;

  delete from public.preference_invitations
  where draft_id = target_draft_id and slot_id = target_slot_id;
  insert into public.preference_invitations (
    token_hash, draft_id, slot_id, owner_id, expires_at
  ) values (
    new_token_hash, target_draft_id, target_slot_id,
    account_user_id, new_expires_at
  );
  return true;
end;
$$;

revoke all on function private.replace_preference_invitation(text, text, text, timestamptz)
  from public;
grant execute on function private.replace_preference_invitation(text, text, text, timestamptz)
  to authenticated;

create or replace function public.replace_preference_invitation(
  target_draft_id text,
  target_slot_id text,
  new_token_hash text,
  new_expires_at timestamptz
)
returns boolean
language sql
security invoker
set search_path = ''
as $$
  select private.replace_preference_invitation(
    target_draft_id, target_slot_id, new_token_hash, new_expires_at
  );
$$;

revoke all on function public.replace_preference_invitation(text, text, text, timestamptz)
  from public;
grant execute on function public.replace_preference_invitation(text, text, text, timestamptz)
  to authenticated;

create or replace function private.claim_preference_invitation(invite_token text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  invitation public.preference_invitations%rowtype;
  target_slot public.preference_slots%rowtype;
  target_trip jsonb;
  invite_hash text;
begin
  if account_user_id is null then
    raise exception 'Sign in before sharing preferences.' using errcode = '42501';
  end if;
  if invite_token is null or pg_catalog.length(invite_token) < 32
    or pg_catalog.length(invite_token) > 256 then
    raise exception 'Preference invitation is invalid.' using errcode = '22023';
  end if;
  invite_hash := pg_catalog.encode(
    extensions.digest(invite_token, 'sha256'), 'hex'
  );
  select * into invitation
  from public.preference_invitations
  where token_hash = invite_hash
  for update;
  if not found then
    raise exception 'Preference invitation is invalid.' using errcode = '22023';
  end if;

  select * into target_slot
  from public.preference_slots
  where draft_id = invitation.draft_id and slot_id = invitation.slot_id
  for update;
  if not found or (
    invitation.expires_at <= pg_catalog.now()
    and target_slot.member_id is distinct from account_user_id
  ) or (
    target_slot.member_id is not null
    and target_slot.member_id <> account_user_id
  ) or exists (
    select 1 from public.preference_slots
    where draft_id = invitation.draft_id
      and member_id = account_user_id
      and slot_id <> invitation.slot_id
  ) then
    raise exception 'This named invitation has already been accepted.' using errcode = '42501';
  end if;
  update public.preference_slots
  set member_id = account_user_id, updated_at = pg_catalog.now()
  where draft_id = invitation.draft_id and slot_id = invitation.slot_id;
  update public.preference_drafts
  set updated_at = pg_catalog.now()
  where draft_id = invitation.draft_id;
  select trip_json into target_trip
  from public.preference_drafts
  where draft_id = invitation.draft_id;

  return pg_catalog.jsonb_build_object(
    'draft_id', target_slot.draft_id,
    'slot_id', target_slot.slot_id,
    'traveler_name', target_slot.traveler_name,
    'trip', target_trip,
    'profile', target_slot.profile_json
  );
end;
$$;

revoke all on function private.claim_preference_invitation(text) from public;
grant execute on function private.claim_preference_invitation(text) to authenticated;

create or replace function public.claim_preference_invitation(invite_token text)
returns jsonb
language sql
security invoker
set search_path = ''
as $$
  select private.claim_preference_invitation(invite_token);
$$;

revoke all on function public.claim_preference_invitation(text) from public;
grant execute on function public.claim_preference_invitation(text) to authenticated;

create or replace function private.submit_preference_profile(
  target_draft_id text,
  target_slot_id text,
  profile_payload jsonb
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  account_user_id uuid := auth.uid();
  expected_name text;
begin
  select traveler_name into expected_name
  from public.preference_slots
  where draft_id = target_draft_id
    and slot_id = target_slot_id
    and member_id = account_user_id
  for update;
  if account_user_id is null or not found
    or profile_payload is null
    or pg_catalog.jsonb_typeof(profile_payload) <> 'object'
    or pg_catalog.octet_length(profile_payload::text) > 16384
    or pg_catalog.lower(pg_catalog.btrim(coalesce(profile_payload ->> 'name', '')))
      <> pg_catalog.lower(pg_catalog.btrim(expected_name))
    or pg_catalog.jsonb_typeof(profile_payload -> 'interests') <> 'array'
    or pg_catalog.jsonb_array_length(profile_payload -> 'interests') not between 1 and 12
    or coalesce(profile_payload ->> 'walking_tolerance', '')
      not in ('low', 'moderate', 'high')
    or pg_catalog.jsonb_typeof(profile_payload -> 'food_restrictions') <> 'array'
    or pg_catalog.jsonb_typeof(profile_payload -> 'must_do_activities') <> 'array' then
    raise exception 'Traveler profile is invalid.' using errcode = '22023';
  end if;

  update public.preference_slots
  set profile_json = profile_payload, updated_at = pg_catalog.now()
  where draft_id = target_draft_id and slot_id = target_slot_id;
  update public.preference_drafts
  set updated_at = pg_catalog.now()
  where draft_id = target_draft_id;
  return true;
end;
$$;

revoke all on function private.submit_preference_profile(text, text, jsonb)
  from public;
grant execute on function private.submit_preference_profile(text, text, jsonb)
  to authenticated;

create or replace function public.submit_preference_profile(
  target_draft_id text,
  target_slot_id text,
  profile_payload jsonb
)
returns boolean
language sql
security invoker
set search_path = ''
as $$
  select private.submit_preference_profile(
    target_draft_id, target_slot_id, profile_payload
  );
$$;

revoke all on function public.submit_preference_profile(text, text, jsonb)
  from public;
grant execute on function public.submit_preference_profile(text, text, jsonb)
  to authenticated;
