-- SuperTory remote schema (Supabase project zspsoestybyvycfurhpw)
-- Reading-invite permission + optional message + suggested-edit review.
-- Run AFTER 20260831090000_reading_invites.sql in the SQL editor.

alter table public.reading_invites
  add column if not exists permission text not null default 'read';

alter table public.reading_invites
  add column if not exists message text;

alter table public.reading_invites
  drop constraint if exists reading_invites_permission_check;

alter table public.reading_invites
  add constraint reading_invites_permission_check
  check (permission in ('read', 'edit'));

update public.reading_invites
  set permission = 'read'
  where permission is null or permission not in ('read', 'edit');

create table if not exists public.reading_invite_edits (
  id uuid primary key default gen_random_uuid(),
  invite_id uuid not null references public.reading_invites (id) on delete cascade,
  scene_id uuid not null references public.reading_invite_scenes (id) on delete cascade,
  commenter_name text,
  edited_text_snapshot text not null default '',
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists reading_invite_edits_invite_idx
  on public.reading_invite_edits (invite_id, created_at desc);

create index if not exists reading_invite_edits_scene_idx
  on public.reading_invite_edits (scene_id, created_at desc);

create table if not exists public.reading_invite_edit_changes (
  id uuid primary key default gen_random_uuid(),
  edit_id uuid not null references public.reading_invite_edits (id) on delete cascade,
  change_index integer not null default 0,
  original_fragment text not null default '',
  replacement_fragment text not null default '',
  start_offset integer not null default 0,
  end_offset integer not null default 0,
  status text not null default 'pending',
  reviewed_at timestamptz
);

alter table public.reading_invite_edit_changes
  drop constraint if exists reading_invite_edit_changes_status_check;

alter table public.reading_invite_edit_changes
  add constraint reading_invite_edit_changes_status_check
  check (status in ('pending', 'accepted', 'rejected'));

create index if not exists reading_invite_edit_changes_edit_idx
  on public.reading_invite_edit_changes (edit_id, change_index);

create index if not exists reading_invite_edit_changes_status_idx
  on public.reading_invite_edit_changes (status, edit_id);

alter table public.reading_invite_edits enable row level security;
alter table public.reading_invite_edit_changes enable row level security;

drop policy if exists reading_invite_edits_select_own on public.reading_invite_edits;
create policy reading_invite_edits_select_own
  on public.reading_invite_edits
  for select
  to authenticated
  using (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id and i.user_id = auth.uid()
    )
  );

drop policy if exists reading_invite_edits_update_own on public.reading_invite_edits;
create policy reading_invite_edits_update_own
  on public.reading_invite_edits
  for update
  to authenticated
  using (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id and i.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1 from public.reading_invites i
      where i.id = invite_id and i.user_id = auth.uid()
    )
  );

drop policy if exists reading_invite_edit_changes_select_own on public.reading_invite_edit_changes;
create policy reading_invite_edit_changes_select_own
  on public.reading_invite_edit_changes
  for select
  to authenticated
  using (
    exists (
      select 1
      from public.reading_invite_edits e
      join public.reading_invites i on i.id = e.invite_id
      where e.id = edit_id and i.user_id = auth.uid()
    )
  );

drop policy if exists reading_invite_edit_changes_update_own on public.reading_invite_edit_changes;
create policy reading_invite_edit_changes_update_own
  on public.reading_invite_edit_changes
  for update
  to authenticated
  using (
    exists (
      select 1
      from public.reading_invite_edits e
      join public.reading_invites i on i.id = e.invite_id
      where e.id = edit_id and i.user_id = auth.uid()
    )
  )
  with check (
    exists (
      select 1
      from public.reading_invite_edits e
      join public.reading_invites i on i.id = e.invite_id
      where e.id = edit_id and i.user_id = auth.uid()
    )
  );

revoke all on public.reading_invite_edits from anon, public;
revoke all on public.reading_invite_edit_changes from anon, public;

grant select, update on public.reading_invite_edits to authenticated;
grant select, update on public.reading_invite_edit_changes to authenticated;

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
    'permission', coalesce(invite_row.permission, 'read'),
    'message', invite_row.message,
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

create or replace function public.submit_reading_invite_edit(
  p_token text,
  p_scene_id uuid,
  p_commenter_name text,
  p_edited_text text,
  p_changes jsonb
)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
  invite_row public.reading_invites%rowtype;
  scene_ok boolean := false;
  edit_id uuid;
  change_item jsonb;
  change_idx integer := 0;
  start_off integer;
  end_off integer;
begin
  if p_token is null or length(trim(p_token)) < 8 then
    raise exception 'invalid_token';
  end if;
  if p_scene_id is null then
    raise exception 'invalid_scene';
  end if;
  if p_changes is null or jsonb_typeof(p_changes) <> 'array' or jsonb_array_length(p_changes) < 1 then
    raise exception 'empty_changes';
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

  if coalesce(invite_row.permission, 'read') <> 'edit' then
    raise exception 'read_only';
  end if;

  select exists (
    select 1 from public.reading_invite_scenes
    where id = p_scene_id and invite_id = invite_row.id
  ) into scene_ok;

  if not scene_ok then
    raise exception 'invalid_scene';
  end if;

  insert into public.reading_invite_edits (
    invite_id, scene_id, commenter_name, edited_text_snapshot
  )
  values (
    invite_row.id,
    p_scene_id,
    nullif(trim(coalesce(p_commenter_name, '')), ''),
    coalesce(p_edited_text, '')
  )
  returning id into edit_id;

  for change_item in
    select value from jsonb_array_elements(p_changes)
  loop
    start_off := coalesce((change_item ->> 'start_offset')::integer, 0);
    end_off := coalesce((change_item ->> 'end_offset')::integer, start_off);
    if end_off < start_off then
      end_off := start_off;
    end if;
    insert into public.reading_invite_edit_changes (
      edit_id,
      change_index,
      original_fragment,
      replacement_fragment,
      start_offset,
      end_offset,
      status
    )
    values (
      edit_id,
      change_idx,
      coalesce(change_item ->> 'original_fragment', ''),
      coalesce(change_item ->> 'replacement_fragment', ''),
      start_off,
      end_off,
      'pending'
    );
    change_idx := change_idx + 1;
  end loop;

  return json_build_object(
    'id', edit_id,
    'invite_id', invite_row.id,
    'scene_id', p_scene_id,
    'change_count', change_idx
  );
end;
$$;

revoke all on function public.get_reading_invite(text) from public;
revoke all on function public.submit_reading_invite_edit(text, uuid, text, text, jsonb) from public;
grant execute on function public.get_reading_invite(text) to anon, authenticated;
grant execute on function public.submit_reading_invite_edit(text, uuid, text, text, jsonb) to anon, authenticated;
