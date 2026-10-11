"""Database enforcement for v3 evidence, including Core/raw SQL updates."""

FIELDS = (
    "id",
    "run_id",
    "skill_version_id",
    "trial_id",
    "origin",
    "scope_key",
    "rendered_hash",
    "content_hash",
    "applicability",
    "selection_policy_version",
    "mode",
    "rank",
    "score",
    "query_terms",
    "created_at",
)
FUNCTION_SQL = (
    "CREATE OR REPLACE FUNCTION protect_v3_skill_selection() RETURNS trigger "
    "LANGUAGE plpgsql AS $$ "
    "BEGIN IF (OLD.content_hash IS NOT NULL OR NEW.content_hash IS NOT NULL) "
    "AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN "
    "RAISE EXCEPTION 'v3 selection evidence is immutable'; END IF; RETURN NEW; END $$"
)
TRIGGER_SQL = (
    "CREATE TRIGGER v3_skill_selection_immutable BEFORE UPDATE ON run_skill_selections "
    "FOR EACH ROW EXECUTE FUNCTION protect_v3_skill_selection()"
)
SQLITE_TRIGGER_SQL = (
    "CREATE TRIGGER v3_skill_selection_immutable BEFORE UPDATE ON run_skill_selections "
    "WHEN (OLD.content_hash IS NOT NULL OR NEW.content_hash IS NOT NULL) AND ("
    + " OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in FIELDS)
    + ") BEGIN SELECT RAISE(ABORT, 'v3 selection evidence is immutable'); END"
)
