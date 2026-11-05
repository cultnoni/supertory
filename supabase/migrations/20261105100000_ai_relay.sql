-- AI 중계 ①단계: 접근 권한 · 사용 기록 · 하루 합계 · 설정 (docs/ai-relay-design.md §4)
-- 원고(프롬프트·응답)는 저장하지 않는다.

create table if not exists public.ai_access (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  plan        text not null default 'beta',
  source      text not null check (source in ('manual','coupon','payment')),
  starts_at   timestamptz not null default now(),
  ends_at     timestamptz,
  daily_usd_override numeric(10,4),
  note        text,
  created_at  timestamptz not null default now()
);
create index if not exists ai_access_user_idx on public.ai_access (user_id);

create table if not exists public.ai_usage (
  id             bigint generated always as identity primary key,
  user_id        uuid not null references auth.users(id) on delete cascade,
  created_at     timestamptz not null default now(),
  day_kst        date not null,
  feature        text not null,
  provider       text not null check (provider in ('gemini','claude')),
  model          text not null,
  input_tokens   integer not null default 0,
  output_tokens  integer not null default 0,
  cache_write_tokens integer not null default 0,
  cache_read_tokens  integer not null default 0,
  cost_usd       numeric(12,6) not null default 0,
  status         text not null,
  latency_ms     integer,
  app_version    text
);
create index if not exists ai_usage_user_day_idx on public.ai_usage (user_id, day_kst);
create index if not exists ai_usage_day_feature_idx on public.ai_usage (day_kst, feature);

create table if not exists public.ai_usage_daily (
  user_id   uuid not null references auth.users(id) on delete cascade,
  day_kst   date not null,
  requests  integer not null default 0,
  cost_usd  numeric(12,6) not null default 0,
  primary key (user_id, day_kst)
);

create table if not exists public.ai_settings (
  id                        smallint primary key default 1 check (id = 1),
  enabled                   boolean not null default true,
  disabled_features         text[] not null default '{}',
  per_user_daily_usd        numeric(10,4),
  per_user_daily_requests   integer,
  global_daily_usd          numeric(10,2),
  model_map                 jsonb not null,
  price_map                 jsonb not null,
  max_output_tokens         jsonb not null,
  updated_at                timestamptz not null default now()
);

-- RLS: 본인 행만 읽기. 쓰기는 서버 함수(service role)만. ai_settings는 앱에서 접근 불가.
alter table public.ai_access       enable row level security;
alter table public.ai_usage        enable row level security;
alter table public.ai_usage_daily  enable row level security;
alter table public.ai_settings     enable row level security;

drop policy if exists ai_access_select_own on public.ai_access;
create policy ai_access_select_own on public.ai_access
  for select to authenticated using (user_id = (select auth.uid()));

drop policy if exists ai_usage_select_own on public.ai_usage;
create policy ai_usage_select_own on public.ai_usage
  for select to authenticated using (user_id = (select auth.uid()));

drop policy if exists ai_usage_daily_select_own on public.ai_usage_daily;
create policy ai_usage_daily_select_own on public.ai_usage_daily
  for select to authenticated using (user_id = (select auth.uid()));

-- 사용 기록 + 하루 합계를 한 번에 (service role 전용)
create or replace function public.ai_record_usage(
  p_user_id uuid,
  p_feature text,
  p_provider text,
  p_model text,
  p_input_tokens integer,
  p_output_tokens integer,
  p_cache_write_tokens integer,
  p_cache_read_tokens integer,
  p_cost_usd numeric,
  p_status text,
  p_latency_ms integer,
  p_app_version text
) returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_day date := (now() at time zone 'Asia/Seoul')::date;
begin
  insert into public.ai_usage (
    user_id, day_kst, feature, provider, model, input_tokens, output_tokens,
    cache_write_tokens, cache_read_tokens, cost_usd, status, latency_ms, app_version
  ) values (
    p_user_id, v_day, p_feature, p_provider, p_model, coalesce(p_input_tokens,0),
    coalesce(p_output_tokens,0), coalesce(p_cache_write_tokens,0),
    coalesce(p_cache_read_tokens,0), coalesce(p_cost_usd,0), p_status,
    p_latency_ms, p_app_version
  );
  insert into public.ai_usage_daily (user_id, day_kst, requests, cost_usd)
  values (p_user_id, v_day, 1, coalesce(p_cost_usd,0))
  on conflict (user_id, day_kst) do update
    set requests = public.ai_usage_daily.requests + 1,
        cost_usd = public.ai_usage_daily.cost_usd + excluded.cost_usd;
end;
$$;
revoke all on function public.ai_record_usage(uuid,text,text,text,integer,integer,integer,integer,numeric,text,integer,text) from public, anon, authenticated;
grant execute on function public.ai_record_usage(uuid,text,text,text,integer,integer,integer,integer,numeric,text,integer,text) to service_role;

-- 전체 하루 원가 합계 (service role 전용)
create or replace function public.ai_global_cost_today() returns numeric
language sql
security definer
set search_path = public
as $$
  select coalesce(sum(cost_usd), 0)
  from public.ai_usage_daily
  where day_kst = (now() at time zone 'Asia/Seoul')::date;
$$;
revoke all on function public.ai_global_cost_today() from public, anon, authenticated;
grant execute on function public.ai_global_cost_today() to service_role;

-- 기본 설정 한 줄. 한도 값은 설계안 §11 결정값(테스터 수는 확정 후 global_daily_usd를 바꾼다).
insert into public.ai_settings (
  id, enabled, per_user_daily_usd, per_user_daily_requests, global_daily_usd,
  model_map, price_map, max_output_tokens
) values (
  1, true, 2.0, 300, 10,
  '{"gemini:default":"gemini-3.5-flash-lite","claude:feedback":"claude-sonnet-5","claude:light":"claude-haiku-5-5"}'::jsonb,
  '{"gemini-3.5-flash-lite":{"in":0.30,"out":2.50},
    "claude-sonnet-5":{"in":2.00,"out":10.00,"cache_write":1.25,"cache_read":0.10},
    "claude-haiku-5-5":{"in":0.10,"out":0.50,"cache_write":1.25,"cache_read":0.10}}'::jsonb,
  '{"gemini-3.5-flash-lite":8192,"claude-sonnet-5":16000,"claude-haiku-5-5":16000}'::jsonb
) on conflict (id) do nothing;
