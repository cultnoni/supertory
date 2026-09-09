-- 092: top-level main_genre='urban' (현대판타지) → fantasy / male / urban
-- so playbook lookup hits fantasy_male__urban instead of a missing urban_* book.

UPDATE project
SET main_genre = 'fantasy',
    sub_genre = 'male',
    genre_detail = 'urban'
WHERE main_genre = 'urban';

INSERT OR IGNORE INTO schema_migration(version, name)
VALUES (92, 'migrate_urban_main_to_fantasy_male');
