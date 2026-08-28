-- SuperTory remote schema (Supabase project zspsoestybyvycfurhpw)
-- Map desktop SQLite scene/project ids onto browser_scenes so a logged-in
-- desktop save can upsert the same row the web editor reads.
-- Apply in the Supabase SQL editor or with `supabase db push`.

alter table public.browser_scenes
  add column if not exists local_scene_id integer;

alter table public.browser_scenes
  add column if not exists local_project_id integer;

comment on column public.browser_scenes.local_scene_id is
  'Desktop SQLite scene.id. NULL for scenes created only in the browser.';

comment on column public.browser_scenes.local_project_id is
  'Desktop SQLite project.id. Same mapping pattern as public.projects.local_project_id.';

-- NULL local_scene_id (browser-only rows) may repeat per user; desktop mirrors
-- must not duplicate the same SQLite scene.
create unique index if not exists browser_scenes_user_id_local_scene_id_key
  on public.browser_scenes (user_id, local_scene_id)
  where local_scene_id is not null;
