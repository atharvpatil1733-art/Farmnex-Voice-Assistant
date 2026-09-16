-- 0001_voice_core.sql
-- Portable schema for the voice assistant component.
-- Everything lives in schema `voice`. Host-app users are referenced by `user_ref text`
-- (Supabase auth uid as text, or any host user id), so the host database can be anything.
--
-- EMBEDDING DIMENSION: 1024 (matches BAAI/bge-m3). If you choose another embedding model,
-- change every `vector(1024)` below BEFORE first ingest, and set EMBEDDING_DIM to match.
-- The backend refuses to start if they differ.

create schema if not exists extensions;   -- exists on Supabase; created for plain Postgres
create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;  -- gen_random_uuid on older PG

create schema if not exists voice;

-- ---------------------------------------------------------------------------
-- User preferences for the assistant (not the host app's profile)
-- ---------------------------------------------------------------------------
create table voice.user_prefs (
  user_ref                     text primary key,
  preferred_language           text not null default 'hi-IN'
                                 check (preferred_language ~ '^[a-z]{2}-[A-Z]{2}$'),
  speaker                      text,
  speech_pace                  real not null default 1.0 check (speech_pace between 0.5 and 2.0),
  input_mode                   text not null default 'push_to_talk'
                                 check (input_mode in ('push_to_talk', 'tap_to_toggle', 'streaming')),
  consent_voice_processing_at  timestamptz,
  created_at                   timestamptz not null default now(),
  updated_at                   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Conversations and messages
-- ---------------------------------------------------------------------------
create table voice.conversations (
  id              uuid primary key default gen_random_uuid(),
  user_ref        text not null,
  pack_id         text not null,
  channel         text not null default 'app' check (channel in ('app', 'text_api', 'eval')),
  language_start  text not null,
  started_at      timestamptz not null default now(),
  ended_at        timestamptz,
  metadata        jsonb not null default '{}'::jsonb
);
create index conversations_user_started_idx on voice.conversations (user_ref, started_at desc);

create table voice.messages (
  id               uuid primary key default gen_random_uuid(),
  conversation_id  uuid not null references voice.conversations (id) on delete cascade,
  turn_id          text not null,
  role             text not null check (role in ('user', 'assistant', 'tool', 'event')),
  content          text not null,
  language         text,
  input_mode       text check (input_mode in ('voice', 'text')),
  stt_confidence   real,
  interrupted      boolean not null default false,
  latency_ms       jsonb,
  provider_info    jsonb,           -- {"llm":"…","stt":"…","tts":"…"} for later comparison
  created_at       timestamptz not null default now()
);
create index messages_conversation_created_idx on voice.messages (conversation_id, created_at);

-- ---------------------------------------------------------------------------
-- Tool audit and confirmation gate
-- ---------------------------------------------------------------------------
create table voice.pending_actions (
  id               uuid primary key default gen_random_uuid(),
  conversation_id  uuid not null references voice.conversations (id) on delete cascade,
  user_ref         text not null,
  tool_name        text not null,
  args             jsonb not null,
  summary          text not null,
  language         text not null,
  status           text not null default 'pending'
                     check (status in ('pending', 'executing', 'executed_ok', 'executed_error',
                                       'cancelled', 'expired')),
  confirmed_via    text check (confirmed_via in ('voice', 'button')),
  idempotency_key  text not null unique,
  expires_at       timestamptz not null,
  created_at       timestamptz not null default now(),
  resolved_at      timestamptz
);
-- At most one open action per conversation.
create unique index pending_actions_one_open_idx
  on voice.pending_actions (conversation_id)
  where status in ('pending', 'executing');

create table voice.tool_invocations (
  id                 uuid primary key default gen_random_uuid(),
  conversation_id    uuid references voice.conversations (id) on delete set null,
  pending_action_id  uuid references voice.pending_actions (id) on delete set null,
  user_ref           text not null,
  turn_id            text,
  tool_name          text not null,
  kind               text not null check (kind in ('read', 'write')),
  args               jsonb not null,
  status             text not null
                       check (status in ('ok', 'error', 'not_found', 'forbidden', 'invalid', 'timeout')),
  error_code         text,
  result_summary     jsonb,          -- trimmed; never full host payloads with personal data
  duration_ms        integer,
  created_at         timestamptz not null default now()
);
create index tool_invocations_user_created_idx on voice.tool_invocations (user_ref, created_at desc);

-- ---------------------------------------------------------------------------
-- Knowledge base (RAG)
-- ---------------------------------------------------------------------------
create table voice.kb_documents (
  id              uuid primary key default gen_random_uuid(),
  pack_id         text not null,
  slug            text not null,
  title           text not null,
  domain          text not null,
  language        text not null,
  version         integer not null check (version > 0),
  status          text not null default 'draft' check (status in ('draft', 'active', 'retired')),
  audience        text,
  source_path     text,
  content_hash    text not null,
  effective_from  timestamptz not null default now(),
  effective_to    timestamptz,
  created_at      timestamptz not null default now(),
  unique (pack_id, slug, language, version)
);
-- Only one active version per (pack, slug, language).
create unique index kb_documents_one_active_idx
  on voice.kb_documents (pack_id, slug, language)
  where status = 'active';

create table voice.kb_chunks (
  id               uuid primary key default gen_random_uuid(),
  document_id      uuid not null references voice.kb_documents (id) on delete cascade,
  pack_id          text not null,
  chunk_index      integer not null,
  heading          text,
  content          text not null,
  language         text not null,
  token_count      integer,
  metadata         jsonb not null default '{}'::jsonb,
  embedding        extensions.vector(1024) not null,
  embedding_model  text not null,
  fts              tsvector generated always as (to_tsvector('simple', coalesce(heading, '') || ' ' || content)) stored,
  created_at       timestamptz not null default now(),
  unique (document_id, chunk_index)
);
create index kb_chunks_embedding_hnsw_idx
  on voice.kb_chunks using hnsw (embedding extensions.vector_cosine_ops);
create index kb_chunks_fts_idx on voice.kb_chunks using gin (fts);
create index kb_chunks_pack_idx on voice.kb_chunks (pack_id);

-- Semantic search over active, currently-effective knowledge.
create or replace function voice.match_chunks(
  p_pack_id          text,
  p_query_embedding  extensions.vector(1024),
  p_match_count      integer default 5,
  p_min_similarity   double precision default 0.3,
  p_domains          text[] default null,
  p_prefer_language  text default null
)
returns table (
  chunk_id     uuid,
  document_id  uuid,
  slug         text,
  version      integer,
  title        text,
  domain       text,
  heading      text,
  content      text,
  language     text,
  similarity   double precision
)
language sql
stable
set search_path = voice, extensions, public
as $$
  with candidates as (
    select c.id, c.document_id, d.slug, d.version, d.title, d.domain, c.heading, c.content,
           c.language,
           1 - (c.embedding <=> p_query_embedding) as raw_similarity
    from voice.kb_chunks c
    join voice.kb_documents d on d.id = c.document_id
    where c.pack_id = p_pack_id
      and d.status = 'active'
      and d.effective_from <= now()
      and (d.effective_to is null or d.effective_to > now())
      and (p_domains is null or d.domain = any (p_domains))
    order by c.embedding <=> p_query_embedding
    limit greatest(p_match_count * 4, 20)
  )
  select id, document_id, slug, version, title, domain, heading, content, language,
         raw_similarity + case when p_prefer_language is not null
                                 and language = p_prefer_language then 0.03 else 0 end
           as similarity
  from candidates
  where raw_similarity >= p_min_similarity
  order by similarity desc
  limit p_match_count;
$$;

-- ---------------------------------------------------------------------------
-- Eval runs
-- ---------------------------------------------------------------------------
create table voice.eval_runs (
  id          uuid primary key default gen_random_uuid(),
  pack_id     text not null,
  suite       text not null,
  config      jsonb not null,      -- providers, models, prompt hash, git sha
  metrics     jsonb not null,
  failures    jsonb not null default '[]'::jsonb,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- The backend connects with a privileged server-side role and bypasses RLS.
-- These policies only matter if you later expose parts of `voice` to clients.
-- ---------------------------------------------------------------------------
alter table voice.user_prefs       enable row level security;
alter table voice.conversations    enable row level security;
alter table voice.messages         enable row level security;
alter table voice.pending_actions  enable row level security;
alter table voice.tool_invocations enable row level security;
alter table voice.kb_documents     enable row level security;
alter table voice.kb_chunks        enable row level security;
alter table voice.eval_runs        enable row level security;

do $$
begin
  -- Supabase-only policies (skipped on plain Postgres where auth.uid() doesn't exist).
  if exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
             where n.nspname = 'auth' and p.proname = 'uid') then
    execute $p$create policy user_prefs_own on voice.user_prefs
               for select to authenticated using (user_ref = auth.uid()::text)$p$;
    execute $p$create policy conversations_own on voice.conversations
               for select to authenticated using (user_ref = auth.uid()::text)$p$;
    execute $p$create policy messages_own on voice.messages
               for select to authenticated using (
                 exists (select 1 from voice.conversations c
                         where c.id = conversation_id and c.user_ref = auth.uid()::text))$p$;
  end if;
end
$$;

comment on schema voice is 'Portable voice assistant component. Not exposed via the Data API by default.';
