-- 005_data_api_grants.sql
--
-- WHY THIS EXISTS
-- From 2025-10-30 Supabase stops auto-granting Data API (PostgREST / supabase-js)
-- access to tables in the `public` schema. Any table created without an explicit
-- GRANT after that date is unreachable through the API (permission denied),
-- including on new projects, preview branches, and local `supabase db reset`.
--
-- Our earlier migrations (001, 002, 004) set up RLS *policies* but relied on the
-- old auto-grant for the underlying table-level privileges. RLS policies decide
-- WHICH ROWS pass; GRANTs decide whether the API can touch the table AT ALL.
-- Both are needed. This migration backfills the GRANTs so a fresh apply / reset /
-- branch reproduces a working project post-2025-10-30.
--
-- This is idempotent: re-granting an existing privilege is a harmless no-op, so it
-- is safe to apply to the live project as well (it changes nothing already granted).
--
-- Access model mirrors the existing RLS policies on these objects:
--   * public read      -> SELECT to anon + authenticated
--   * service write     -> full DML to service_role (also bypasses RLS via its key)
--   * authenticated write is included to match the "Service write access FOR ALL"
--     policies already present; RLS still gates it, so this only opens the door.

-- ---------------------------------------------------------------------------
-- Base tables
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    t text;
    tbls text[] := ARRAY[
        'snapshots', 'teams', 'players', 'player_snapshots', 'price_changes',
        'events', 'fixtures', 'csv_name_mapping', 'csv_imports',
        'ext_model_predictions', 'projection_sources', 'projection_inputs',
        'projection_captures'
    ];
BEGIN
    FOREACH t IN ARRAY tbls LOOP
        -- only grant if the table actually exists in this database, so a partial
        -- schema (or future removal) doesn't make the migration fail
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) THEN
            EXECUTE format('GRANT SELECT ON public.%I TO anon;', t);
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO authenticated;', t);
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO service_role;', t);
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Views (read-only). final_projections already had a SELECT grant in 004;
-- re-granting is a no-op. The v_* helper views are exposed for the same
-- public-read access as their underlying tables.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v text;
    vws text[] := ARRAY[
        'final_projections', 'v_player_latest', 'v_price_timeline',
        'v_projections', 'v_current_projections'
    ];
BEGIN
    FOREACH v IN ARRAY vws LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.views
            WHERE table_schema = 'public' AND table_name = v
        ) THEN
            EXECUTE format('GRANT SELECT ON public.%I TO anon, authenticated, service_role;', v);
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Sequences: INSERTs through the API need USAGE on the identity/serial
-- sequences too. Grant across all sequences currently in public.
-- ---------------------------------------------------------------------------
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Default privileges: make sure any table/view/sequence WE create in future
-- migrations inherits these grants automatically, so we don't have to remember
-- to hand-grant every new object after 2025-10-30. (Applies to objects created
-- by the current migration role.)
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO authenticated, service_role;
