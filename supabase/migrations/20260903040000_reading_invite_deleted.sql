-- Soft-delete reading invites (hide from writer list, block public token).
-- Run AFTER 20260903020000_reading_invite_edits.sql in the SQL editor.
-- get_reading_invite() already requires status = 'active', so deleted tokens
-- return null and the homepage shows the existing invalid-link message.

alter table public.reading_invites
  drop constraint if exists reading_invites_status_check;

alter table public.reading_invites
  add constraint reading_invites_status_check
  check (status in ('active', 'revoked', 'deleted'));

grant delete on public.reading_invites to authenticated;

drop policy if exists reading_invites_delete_own on public.reading_invites;
create policy reading_invites_delete_own
  on public.reading_invites
  for delete
  to authenticated
  using (auth.uid() = user_id);
