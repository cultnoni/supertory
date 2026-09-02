# SuperTory Supabase

Remote tables for account sync and the browser web-editor pilot live here (not the local SQLite `db/` migrations).

Project ref: `zspsoestybyvycfurhpw`

- `user_settings` / `document_conflict_backups`: desktop + later multi-device sync
- `browser_scenes`: web editor store; desktop saves of a logged-in user upsert here
  (`local_scene_id` / `local_project_id` map SQLite ids so the same scene is not duplicated).
  The homepage editor lists both browser-only and desktop-mirrored rows. Browser saves
  apply `resolve_write` rules in the client for this pilot; server-side enforcement
  (Edge Function) is a later step. Browser → desktop SQLite pull is phase 4.
- `reading_invites` / `reading_invite_scenes` / `reading_invite_comments`:
  desktop snapshot links for beta readers (`/read/{token}` on the homepage).
  Public readers use `get_reading_invite(p_token)` — table SELECT is not granted to anon.
- `reading_invite_edits` / `reading_invite_edit_changes`:
  suggested manuscript edits from `permission=edit` links. Public submit is
  `submit_reading_invite_edit(...)` only (SECURITY DEFINER). Writers review
  accept/reject in the desktop app.

Apply new files under `migrations/` in the Supabase SQL editor, or with the Supabase CLI (`supabase db push`) against that project. Live schema is **not** auto-applied from the desktop app — run `20260903020000_reading_invite_edits.sql` then `20260903040000_reading_invite_deleted.sql` in the SQL editor (edit-permission invites, then `status='deleted'` soft-delete).
