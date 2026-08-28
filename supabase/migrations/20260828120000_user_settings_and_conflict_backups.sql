-- SuperTory remote schema (Supabase project zspsoestybyvycfurhpw)
-- Multi-device primary-wins conflict policy + losing-version backups.
-- Apply in the Supabase SQL editor or with `supabase db push`.

create table if not exists public.user_settings (
  user_id uuid primary key references auth.users (id) on delete cascade,
  primary_device_type text not null
    check (primary_device_type in ('desktop', 'browser', 'mobile')),
  conflict_policy_agreed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.document_conflict_backups (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  source_table text not null,
  source_record_id text not null,
  losing_device_type text not null
    check (losing_device_type in ('desktop', 'browser', 'mobile')),
  content_snapshot jsonb not null,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists document_conflict_backups_user_created_idx
  on public.document_conflict_backups (user_id, created_at desc);

-- Losing versions are kept for 30-day recovery; prune in a later job.

create or replace function public.supertory_set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

drop trigger if exists user_settings_set_updated_at on public.user_settings;
create trigger user_settings_set_updated_at
  before update on public.user_settings
  for each row
  execute procedure public.supertory_set_updated_at();

alter table public.user_settings enable row level security;
alter table public.document_conflict_backups enable row level security;

drop policy if exists user_settings_select_own on public.user_settings;
create policy user_settings_select_own
  on public.user_settings
  for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists user_settings_insert_own on public.user_settings;
create policy user_settings_insert_own
  on public.user_settings
  for insert
  to authenticated
  with check (auth.uid() = user_id);

drop policy if exists user_settings_update_own on public.user_settings;
create policy user_settings_update_own
  on public.user_settings
  for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists document_conflict_backups_select_own
  on public.document_conflict_backups;
create policy document_conflict_backups_select_own
  on public.document_conflict_backups
  for select
  to authenticated
  using (auth.uid() = user_id);

drop policy if exists document_conflict_backups_insert_own
  on public.document_conflict_backups;
create policy document_conflict_backups_insert_own
  on public.document_conflict_backups
  for insert
  to authenticated
  with check (auth.uid() = user_id);

grant select, insert, update on public.user_settings to authenticated;
grant select, insert on public.document_conflict_backups to authenticated;
