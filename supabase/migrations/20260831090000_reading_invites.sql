-- SuperTory remote schema (Supabase project zspsoestybyvycfurhpw)
-- Snapshot reading invites + public RPCs.
-- Run AFTER 20260831031500_reading_invite_comments.sql in the SQL editor.
-- CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS so a first-run homepage
-- comments file (or this file alone) does not error. Aligns leftover homepage
-- columns (scene_id, commenter_name, body) to the RPC contract.

create table if not exists public.reading_invites (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users (id) on delete cascade,
  token text not null,
  project_local_id integer,
  title text not null default '',
  status text not null default 'active' check (status in ('active', 'revoked')),
  created_at timestamptz not null default timezone('utc', now()),
  expires_at timestamptz not null default (timezone('utc', now()) + interval '30 days')
);

alter table public.reading_invites
  add column if not exists user_id uuid references auth.users (id) on delete cascade;
alter table public.reading_invites
  add column if not exists project_local_id integer;
alter table public.reading_invites
  add column if not exists title text not null default '';
alter table public.reading_invites
  add column if not exists status text not null default 'active';
alter table public.reading_invites
  add column if not exists created_at timestamptz not null default timezone('utc', now());
alter table public.reading_invites
  add column if not exists expires_at timestamptz not null default (timezone('utc', now()) + interval '30 days');

create unique index if not exists reading_invites_token_uidx
  on public.reading_invites (token);

create unique index if not exists reading_invites_token_key
  on public.reading_invites (token);

create index if not exists reading_invites_user_project_idx
  on public.reading_invites (user_id, project_local_id, created_at desc);

create table if not exists public.reading_invite_scenes (
  id uuid primary key default gen_random_uuid(),
  invite_id uuid not null references public.reading_invites (id) on delete cascade,
  order_index integer not null default 0,
  scene_title text not null default '',
  content_snapshot text not null default '',
  local_scene_id integer
);

alter table public.reading_invite_scenes
  add column if not exists local_scene_id integer;

create index if not exists reading_invite_scenes_invite_idx
  on public.reading_invite_scenes (invite_id, order_index);

create index if not exists reading_invite_scenes_invite_order_idx
  on public.reading_invite_scenes (invite_id, order_index);

create table if not exists public.reading_invite_comments (
  id uuid primary key default gen_random_uuid(),
  invite_id uuid not null references public.reading_invites (id) on delete cascade,
  invite_scene_id uuid references public.reading_invite_scenes (id) on delete set null,
  author_name text not null default '',
  content text not null default '',
  created_at timestamptz not null default timezone('utc', now())
);

alter table public.reading_invite_comments
  add column if not exists invite_scene_id uuid references public.reading_invite_scenes (id) on delete set null;
alter table public.reading_invite_comments
  add column if not exists author_name text not null default '';
alter table public.reading_invite_comments
  add column if not exists content text not null default '';

-- Older homepage draft used scene_id / commenter_name / body (NOT NULL, no default).
-- Relax those so RPC inserts that only set canonical columns still succeed.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'reading_invite_comments'
      and column_name = 'scene_id'
  ) then
    alter table public.reading_invite_comments alter column scene_id drop not null;
    update public.reading_invite_comments
      set invite_scene_id = scene_id
      where invite_scene_id is null and scene_id is not null;
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'reading_invite_comments'
      and column_name = 'commenter_name'
  ) then
    update public.reading_invite_comments
      set author_name = commenter_name
      where coalesce(author_name, '') = '' and coalesce(commenter_name, '') <> '';
  end if;
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'reading_invite_comments'
      and column_name = 'body'
  ) then
    alter table public.reading_invite_comments alter column body drop not null;
    alter table public.reading_invite_comments alter column body set default '';
    update public.reading_invite_comments
      set content = body
      where coalesce(content, '') = '' and coalesce(body, '') <> '';
  end if;
end
$$;

create index if not exists reading_invite_comments_invite_idx
  on public.reading_invite_comments (invite_id, created_at desc);

create index if not exists reading_invite_comments_invite_created_idx
  on public.reading_invite_comments (invite_id, created_at desc);

create index if not exists reading_invite_comments_scene_idx
  on public.reading_invite_comments (invite_scene_id, created_at desc);

alter table public.reading_invites enable row level security;
alter table public.reading_invite_scenes enable row level security;
alter table public.reading_invite_comments enable row level security;

-- Drop homepage public table-SELECT (would leak every active token).
drop policy if exists reading_invites_select_public on public.reading_invites;
drop policy if exists reading_invite_scenes_select_public on public.reading_invite_scenes;
drop policy if exists reading_invite_comments_insert_active on public.reading_invite_comments;

drop policy if exists reading_invites_select_own on public.reading_invites;
create policy reading_invites_select_own
  on public.reading_invites
  for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists reading_invites_insert_own on public.reading_invites;
create policy reading_invites_insert_own
  on public.reading_invites
  for insert
  to authenticated
  with check (auth.uid() = user_id);

drop policy if exists reading_invites_update_own on public.reading_invites;
create policy reading_invites_update_own
  on public.reading_invites
  for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists reading_invite_scenes_select_own on public.reading_invite_scenes;
create policy reading_invite_scenes_select_own
  on public.reading_invite_scenes
  for select
  to authenticated
  using (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id and i.user_id = auth.uid()
    )
  );

drop policy if exists reading_invite_scenes_insert_own on public.reading_invite_scenes;
create policy reading_invite_scenes_insert_own
  on public.reading_invite_scenes
  for insert
  to authenticated
  with check (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id and i.user_id = auth.uid()
    )
  );

drop policy if exists reading_invite_comments_select_own on public.reading_invite_comments;
create policy reading_invite_comments_select_own
  on public.reading_invite_comments
  for select
  to authenticated
  using (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id and i.user_id = auth.uid()
    )
  );

drop policy if exists reading_invite_comments_insert_public on public.reading_invite_comments;
create policy reading_invite_comments_insert_public
  on public.reading_invite_comments
  for insert
  to anon, authenticated
  with check (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id
        and i.status = 'active'
        and i.expires_at > timezone('utc', now())
    )
  );

revoke all on public.reading_invites from anon, public;
revoke all on public.reading_invite_scenes from anon, public;
revoke all on public.reading_invite_comments from anon, public;

grant select, insert, update on public.reading_invites to authenticated;
grant select, insert on public.reading_invite_scenes to authenticated;
grant select on public.reading_invite_comments to authenticated;
grant insert on public.reading_invite_comments to anon, authenticated;

create or replace function public.get_reading_invite(p_token text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  invite_row public.reading_invites%rowtype;
  result json;
begin
  if p_token is null or length(trim(p_token)) < 8 then
    return null;
  end if;

  select * into invite_row
  from public.reading_invites
  where token = trim(p_token)
    and status = 'active'
    and expires_at > timezone('utc', now())
  limit 1;

  if not found then
    return null;
  end if;

  select json_build_object(
    'id', invite_row.id,
    'title', invite_row.title,
    'expires_at', invite_row.expires_at,
    'created_at', invite_row.created_at,
    'scenes', coalesce((
      select json_agg(
        json_build_object(
          'id', s.id,
          'order_index', s.order_index,
          'scene_title', s.scene_title,
          'content_snapshot', s.content_snapshot
        )
        order by s.order_index, s.id
      )
      from public.reading_invite_scenes s
      where s.invite_id = invite_row.id
    ), '[]'::json)
  ) into result;

  return result;
end;
$$;

create or replace function public.add_reading_invite_comment(
  p_token text,
  p_invite_scene_id uuid,
  p_author_name text,
  p_content text
)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  invite_row public.reading_invites%rowtype;
  new_id uuid;
begin
  if p_token is null or length(trim(p_token)) < 8 then
    raise exception 'invalid_token';
  end if;
  if p_content is null or length(trim(p_content)) = 0 then
    raise exception 'empty_comment';
  end if;

  select * into invite_row
  from public.reading_invites
  where token = trim(p_token)
    and status = 'active'
    and expires_at > timezone('utc', now())
  limit 1;

  if not found then
    raise exception 'invalid_token';
  end if;

  if p_invite_scene_id is not null then
    if not exists (
      select 1 from public.reading_invite_scenes
      where id = p_invite_scene_id and invite_id = invite_row.id
    ) then
      raise exception 'invalid_scene';
    end if;
  end if;

  insert into public.reading_invite_comments (
    invite_id, invite_scene_id, author_name, content
  )
  values (
    invite_row.id,
    p_invite_scene_id,
    coalesce(trim(p_author_name), ''),
    trim(p_content)
  )
  returning id into new_id;

  return json_build_object('id', new_id, 'invite_id', invite_row.id);
end;
$$;

revoke all on function public.get_reading_invite(text) from public;
revoke all on function public.add_reading_invite_comment(text, uuid, text, text) from public;
grant execute on function public.get_reading_invite(text) to anon, authenticated;
grant execute on function public.add_reading_invite_comment(text, uuid, text, text) to anon, authenticated;
