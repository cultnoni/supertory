# SuperTory SQLite schema

Apply [`001_initial_schema.sql`](001_initial_schema.sql) once to a new SQLite
database. The application must run these connection-local settings every time it
opens a connection:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
```

`scene_revision.word_count` is supplied by the editor because word boundaries
depend on the project language and Markdown handling. Save a new revision by
inserting it with `is_current = 0`, then swapping the old and new flags in one
`UPDATE` statement inside the same transaction. The partial unique index keeps
the final state to one current revision.

The two FTS5 tables use external-content tables and are maintained by triggers.
After a manual import or repair, rebuild either index with:

```sql
INSERT INTO scene_fts(scene_fts) VALUES ('rebuild');
INSERT INTO character_fts(character_fts) VALUES ('rebuild');
```

Run the schema contract suite with `python -m unittest tests.test_schema -v`.
