-- SuperTory remote schema (Supabase project zspsoestybyvycfurhpw)
-- Browser-only scene store for the web editor pilot.
-- Not synced with desktop SQLite in this step (sync is phase 3).
-- Apply in the Supabase SQL editor or with `supabase db push`.

create table if not exists public.browser_scenes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  title text not null default '',
  content_html text not null default '',
  row_version integer not null default 1 check (row_version > 0),
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists browser_scenes_user_updated_idx
  on public.browser_scenes (user_id, updated_at desc);

create or replace function public.supertory_set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists browser_scenes_set_updated_at on public.browser_scenes;
create trigger browser_scenes_set_updated_at
  before update on public.browser_scenes
  for each row
  execute procedure public.supertory_set_updated_at();

alter table public.browser_scenes enable row level security;

drop policy if exists browser_scenes_select_own on public.browser_scenes;
create policy browser_scenes_select_own
  on public.browser_scenes
  for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists browser_scenes_insert_own on public.browser_scenes;
create policy browser_scenes_insert_own
  on public.browser_scenes
  for insert
  to authenticated
  with check (auth.uid() = user_id);

drop policy if exists browser_scenes_update_own on public.browser_scenes;
create policy browser_scenes_update_own
  on public.browser_scenes
  for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

grant select, insert, update on public.browser_scenes to authenticated;
