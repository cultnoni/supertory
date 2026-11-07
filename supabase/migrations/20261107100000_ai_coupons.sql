-- AI 중계 ②단계: 쿠폰 (docs/ai-relay-design.md §9)
-- 쿠폰을 등록하면 ai_access에 source='coupon' 한 줄이 들어간다. 중계 창구는 바꾸지 않는다.
-- 쿠폰 코드는 평문으로 저장하지 않고 해시(sha256)만 저장한다.

create table if not exists public.coupons (
  id            uuid primary key default gen_random_uuid(),
  code_hash     text not null unique,            -- sha256(정규화한 코드), hex
  code_hint     text not null,                   -- 관리자 식별용 끝 4자리
  plan          text not null default 'beta',
  duration_days integer not null check (duration_days > 0),
  max_uses      integer not null default 1 check (max_uses > 0),
  used_count    integer not null default 0 check (used_count >= 0),
  daily_usd_override numeric(10,4),              -- 이 쿠폰으로 받는 사용자별 하루 상한 예외(null = 기본값)
  expires_at    timestamptz,                     -- 이 시각 이후엔 등록 불가(null = 기한 없음)
  disabled      boolean not null default false,
  note          text,
  created_at    timestamptz not null default now(),
  check (used_count <= max_uses)
);

create table if not exists public.coupon_redemptions (
  id          uuid primary key default gen_random_uuid(),
  coupon_id   uuid not null references public.coupons(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  access_id   uuid references public.ai_access(id) on delete set null,
  redeemed_at timestamptz not null default now(),
  unique (coupon_id, user_id)                    -- 한 사람이 같은 쿠폰을 두 번 쓰지 못한다
);
create index if not exists coupon_redemptions_user_idx on public.coupon_redemptions (user_id);

-- 실패 시도 기록(무작위 대입 방지용). 앱에서는 접근 불가.
create table if not exists public.coupon_attempts (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references auth.users(id) on delete cascade,
  ok         boolean not null,
  created_at timestamptz not null default now()
);
create index if not exists coupon_attempts_user_idx on public.coupon_attempts (user_id, created_at);
alter table public.coupon_attempts enable row level security;

-- RLS: 쿠폰 표는 앱에서 읽기·쓰기 모두 불가(코드 추측 방지). 본인 등록 내역만 읽을 수 있다.
alter table public.coupons            enable row level security;
alter table public.coupon_redemptions enable row level security;

drop policy if exists coupon_redemptions_select_own on public.coupon_redemptions;
create policy coupon_redemptions_select_own on public.coupon_redemptions
  for select to authenticated using (user_id = (select auth.uid()));

-- 코드 정규화: 공백·하이픈 제거, 대문자
create or replace function public.coupon_normalize(p_code text) returns text
language sql
immutable
as $$
  select upper(regexp_replace(coalesce(p_code, ''), '[\s-]', '', 'g'));
$$;

-- 쿠폰 등록. 로그인한 사용자가 호출한다(auth.uid()). 실패 사유는 코드로만 알려 준다.
-- 결과: { ok: true, ends_at } 또는 { ok: false, code: invalid | expired | used_up | already_redeemed | login_required | rate_limited }
create or replace function public.redeem_coupon(p_code text) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user   uuid := auth.uid();
  v_norm   text := public.coupon_normalize(p_code);
  v_hash   text;
  v_coupon public.coupons%rowtype;
  v_access uuid;
  v_ends   timestamptz;
  v_fails  integer;
begin
  if v_user is null then
    return jsonb_build_object('ok', false, 'code', 'login_required');
  end if;

  -- 무작위 대입 방지: 최근 10분 실패 5번이면 잠시 막는다.
  select count(*) into v_fails
  from public.coupon_attempts
  where user_id = v_user and ok = false and created_at > now() - interval '10 minutes';
  if v_fails >= 5 then
    return jsonb_build_object('ok', false, 'code', 'rate_limited');
  end if;

  v_hash := encode(sha256(convert_to(v_norm, 'UTF8')), 'hex');

  -- 같은 쿠폰의 동시 등록이 사용 횟수를 넘기지 않도록 행을 잠근다.
  select * into v_coupon from public.coupons where code_hash = v_hash for update;

  if not found or v_coupon.disabled then
    insert into public.coupon_attempts (user_id, ok) values (v_user, false);
    return jsonb_build_object('ok', false, 'code', 'invalid');
  end if;
  if v_coupon.expires_at is not null and v_coupon.expires_at <= now() then
    insert into public.coupon_attempts (user_id, ok) values (v_user, false);
    return jsonb_build_object('ok', false, 'code', 'expired');
  end if;
  if exists (select 1 from public.coupon_redemptions where coupon_id = v_coupon.id and user_id = v_user) then
    return jsonb_build_object('ok', false, 'code', 'already_redeemed');
  end if;
  if v_coupon.used_count >= v_coupon.max_uses then
    insert into public.coupon_attempts (user_id, ok) values (v_user, false);
    return jsonb_build_object('ok', false, 'code', 'used_up');
  end if;

  -- 이미 쓸 수 있는 기간이 남아 있으면 그 끝에서부터 이어 붙인다.
  select max(ends_at) into v_ends
  from public.ai_access
  where user_id = v_user and ends_at is not null and ends_at > now();
  v_ends := coalesce(v_ends, now()) + make_interval(days => v_coupon.duration_days);

  insert into public.ai_access (user_id, plan, source, starts_at, ends_at, daily_usd_override, note)
  values (v_user, v_coupon.plan, 'coupon', now(), v_ends, v_coupon.daily_usd_override,
          'coupon ' || v_coupon.code_hint)
  returning id into v_access;

  update public.coupons set used_count = used_count + 1 where id = v_coupon.id;
  insert into public.coupon_redemptions (coupon_id, user_id, access_id)
  values (v_coupon.id, v_user, v_access);
  insert into public.coupon_attempts (user_id, ok) values (v_user, true);

  return jsonb_build_object('ok', true, 'ends_at', v_ends);
end;
$$;

revoke all on function public.redeem_coupon(text) from public, anon;
grant execute on function public.redeem_coupon(text) to authenticated;
revoke all on function public.coupon_normalize(text) from public, anon, authenticated;

-- 관리자 발급(service role 전용): 코드를 받아 해시로 저장한다. 코드는 발급자가 따로 보관한다.
create or replace function public.admin_create_coupon(
  p_code text,
  p_duration_days integer,
  p_max_uses integer default 1,
  p_expires_at timestamptz default null,
  p_daily_usd_override numeric default null,
  p_note text default null
) returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_norm text := public.coupon_normalize(p_code);
  v_id   uuid;
begin
  if length(v_norm) < 10 then
    raise exception 'coupon code too short (min 10 chars)';
  end if;
  insert into public.coupons (code_hash, code_hint, duration_days, max_uses, expires_at, daily_usd_override, note)
  values (encode(sha256(convert_to(v_norm, 'UTF8')), 'hex'), right(v_norm, 4),
          p_duration_days, p_max_uses, p_expires_at, p_daily_usd_override, p_note)
  returning id into v_id;
  return v_id;
end;
$$;
revoke all on function public.admin_create_coupon(text,integer,integer,timestamptz,numeric,text) from public, anon, authenticated;
grant execute on function public.admin_create_coupon(text,integer,integer,timestamptz,numeric,text) to service_role;
