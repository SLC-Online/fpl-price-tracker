-- Projection capture history
-- ============================
-- Problem: previously each scrape UPSERTed projection_inputs on
-- (source_id, element_id, season, gameweek, uploaded_for_gw), so re-scraping
-- overwrote the previous values. We kept only the latest capture and lost history.
--
-- Fix: record every DISTINCT capture as its own row in projection_captures,
-- with a content hash for deduplication. projection_inputs rows link to a
-- capture via capture_id. Re-scraping identical data creates NO new capture
-- (content_hash matches); changed data creates a new timestamped capture and
-- a fresh set of projection_inputs rows.
--
-- The app reads the LATEST capture per (source, uploaded_for_gw) via
-- final_projections (recreated below).

CREATE TABLE IF NOT EXISTS projection_captures (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES projection_sources(id),
    season TEXT NOT NULL,
    uploaded_for_gw INTEGER NOT NULL,   -- which GW this projection set was captured before
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    content_hash TEXT NOT NULL,         -- hash of the projection payload for dedup
    row_count INTEGER NOT NULL DEFAULT 0,
    player_count INTEGER NOT NULL DEFAULT 0,
    -- Optional provenance (e.g. Patreon post published_at, CSV filename)
    meta JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pc_source_gw ON projection_captures(source_id, uploaded_for_gw, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_pc_hash ON projection_captures(source_id, uploaded_for_gw, content_hash);

-- Link projection_inputs rows to their capture.
ALTER TABLE projection_inputs ADD COLUMN IF NOT EXISTS capture_id BIGINT REFERENCES projection_captures(id);
CREATE INDEX IF NOT EXISTS idx_pi_capture ON projection_inputs(capture_id);

-- Drop the old uniqueness constraint that forced overwrite behaviour.
-- (Name from migration 002; guard in case it was auto-named.)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'projection_inputs_source_id_element_id_season_gameweek_uploa_key'
    ) THEN
        ALTER TABLE projection_inputs
            DROP CONSTRAINT projection_inputs_source_id_element_id_season_gameweek_uploa_key;
    END IF;
END $$;

-- New uniqueness: one row per (capture, element, gameweek).
-- Multiple captures for the same (source, uploaded_for_gw) now coexist.
ALTER TABLE projection_inputs
    DROP CONSTRAINT IF EXISTS projection_inputs_capture_unique;
ALTER TABLE projection_inputs
    ADD CONSTRAINT projection_inputs_capture_unique
    UNIQUE (capture_id, element_id, gameweek);

-- Backfill: wrap any existing (pre-capture) projection_inputs rows into a
-- capture per (source_id, uploaded_for_gw) so nothing disappears from the app
-- during the transition. Each existing (source, uploaded_for_gw) group becomes
-- one "migrated" capture, and its rows get that capture_id.
DO $$
DECLARE
    grp RECORD;
    new_id BIGINT;
BEGIN
    FOR grp IN
        SELECT source_id, season, uploaded_for_gw,
               COUNT(*) AS rows, COUNT(DISTINCT element_id) AS players
        FROM projection_inputs
        WHERE capture_id IS NULL
        GROUP BY source_id, season, uploaded_for_gw
    LOOP
        INSERT INTO projection_captures
            (source_id, season, uploaded_for_gw, content_hash, row_count, player_count, meta)
        VALUES
            (grp.source_id, grp.season, grp.uploaded_for_gw,
             'migrated-' || grp.source_id || '-' || grp.uploaded_for_gw,
             grp.rows, grp.players, '{"migrated": true}'::jsonb)
        RETURNING id INTO new_id;

        UPDATE projection_inputs
        SET capture_id = new_id
        WHERE capture_id IS NULL
          AND source_id = grp.source_id
          AND season = grp.season
          AND uploaded_for_gw = grp.uploaded_for_gw;
    END LOOP;
END $$;

-- RLS for the new table (public read, service write — same as siblings)
ALTER TABLE projection_captures ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read access" ON projection_captures;
DROP POLICY IF EXISTS "Service write access" ON projection_captures;
CREATE POLICY "Public read access" ON projection_captures FOR SELECT USING (true);
CREATE POLICY "Service write access" ON projection_captures FOR ALL USING (true) WITH CHECK (true);

-- Recreate final_projections to read the LATEST capture per (source, uploaded_for_gw).
-- Currently only transfer_algorithm feeds the app's numbers.
DROP VIEW IF EXISTS final_projections;
CREATE VIEW final_projections AS
WITH latest_capture AS (
    SELECT DISTINCT ON (pc.source_id, pc.uploaded_for_gw)
        pc.id AS capture_id, pc.source_id, pc.uploaded_for_gw
    FROM projection_captures pc
    JOIN projection_sources ps ON ps.id = pc.source_id
    WHERE ps.source_name = 'transfer_algorithm'
    ORDER BY pc.source_id, pc.uploaded_for_gw, pc.captured_at DESC
)
SELECT
    pi.element_id,
    pi.gameweek,
    pi.expected_points,
    pi.uploaded_for_gw,
    pi.season,
    pi.meta
FROM projection_inputs pi
JOIN latest_capture lc ON lc.capture_id = pi.capture_id;

GRANT SELECT ON final_projections TO anon, authenticated;
