-- Run once in Supabase SQL Editor. Server-side app access uses SUPABASE_SECRET_KEY.
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

-- Shared, Pydantic-validated activity catalog. This is edited only through
-- the admin-protected Curate catalog workspace.
create table if not exists public.catalog_activities (
  activity_id text primary key,
  activity_json jsonb not null,
  updated_at timestamptz not null default now()
);

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
