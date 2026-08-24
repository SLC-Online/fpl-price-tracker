-- Projections Abstraction Layer
-- This is the UNIFIED source of expected points used for all calculations.
-- Currently populated from Transfer Algorithm CSV, but designed to accept
-- any number of input sources in future (our own model, FPLReview, etc.)

-- Raw projections from individual sources
CREATE TABLE projection_sources (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,          -- 'transfer_algorithm', 'fpl_review', 'our_model_v1', etc.
    description TEXT,
    weight REAL DEFAULT 1.0,            -- how much this source contributes to the composite
    active BOOLEAN DEFAULT TRUE,        -- can be toggled off without deleting
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Per-source, per-player, per-GW projections
CREATE TABLE projection_inputs (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES projection_sources(id),
    element_id INTEGER NOT NULL REFERENCES players(element_id),
    season TEXT NOT NULL,
    gameweek INTEGER NOT NULL,          -- the GW being projected FOR
    uploaded_for_gw INTEGER NOT NULL,   -- the GW the data was uploaded BEFORE (context)
    expected_points REAL,
    confidence REAL,                    -- optional: how confident is this source
    meta JSONB DEFAULT '{}',            -- any extra source-specific data (BCV, fixture ratio, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, element_id, season, gameweek, uploaded_for_gw)
);

-- THE COMPOSITE VIEW: this is what all features query against.
-- Merges all active sources using their weights.
-- When there's only one source (Transfer Algorithm), it's just that source's numbers.
-- When we add more sources, it automatically blends them.
CREATE VIEW v_projections AS
SELECT
    pi.element_id,
    pi.season,
    pi.gameweek,
    pi.uploaded_for_gw,
    p.web_name,
    p.team_id,
    -- Weighted average expected points across all active sources
    SUM(pi.expected_points * ps.weight) / NULLIF(SUM(ps.weight), 0) AS expected_points,
    -- Max confidence across sources
    MAX(pi.confidence) AS confidence,
    -- Number of sources contributing
    COUNT(DISTINCT ps.id) AS source_count
FROM projection_inputs pi
JOIN projection_sources ps ON pi.source_id = ps.id
JOIN players p ON pi.element_id = p.element_id
WHERE ps.active = TRUE
GROUP BY pi.element_id, pi.season, pi.gameweek, pi.uploaded_for_gw, p.web_name, p.team_id;

-- Convenience view: latest projections for the upcoming GWs
-- (uses the most recent uploaded_for_gw)
CREATE VIEW v_current_projections AS
SELECT * FROM v_projections
WHERE uploaded_for_gw = (
    SELECT MAX(uploaded_for_gw) FROM projection_inputs
    JOIN projection_sources ps ON projection_inputs.source_id = ps.id
    WHERE ps.active = TRUE
);

-- Indexes
CREATE INDEX idx_pi_element_season ON projection_inputs(element_id, season);
CREATE INDEX idx_pi_source_gw ON projection_inputs(source_id, uploaded_for_gw);
CREATE INDEX idx_pi_lookup ON projection_inputs(element_id, season, gameweek, uploaded_for_gw);

-- RLS
ALTER TABLE projection_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE projection_inputs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Public read access" ON projection_sources FOR SELECT USING (true);
CREATE POLICY "Public read access" ON projection_inputs FOR SELECT USING (true);
CREATE POLICY "Service write access" ON projection_sources FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON projection_inputs FOR ALL USING (true) WITH CHECK (true);

-- Insert the Transfer Algorithm as our first source
INSERT INTO projection_sources (source_name, description, weight, active)
VALUES ('transfer_algorithm', 'Transfer Algorithm Patreon CSV - BCV and projected points per GW', 1.0, true);
