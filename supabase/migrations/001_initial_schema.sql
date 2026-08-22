-- FPL Tracker: Initial Schema
-- Run this in Supabase SQL Editor to set up the database

-- ============================================================
-- CORE TABLES
-- ============================================================

-- Snapshot metadata: one row per scrape run
CREATE TABLE snapshots (
    snapshot_id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL UNIQUE,
    source TEXT DEFAULT 'local',  -- 'local', 'github-actions', 'supabase-cron'
    players_count INTEGER
);

-- Teams
CREATE TABLE teams (
    team_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    code INTEGER NOT NULL
);

-- Player master data (latest known state, updated each scrape)
CREATE TABLE players (
    element_id INTEGER PRIMARY KEY,
    first_name TEXT,
    second_name TEXT,
    web_name TEXT NOT NULL,
    team_id INTEGER REFERENCES teams(team_id),
    element_type INTEGER,  -- 1=GK, 2=DEF, 3=MID, 4=FWD
    code INTEGER,  -- used for photo URLs
    updated_at TIMESTAMPTZ
);

-- The big time-series table: one row per player per snapshot
CREATE TABLE player_snapshots (
    snapshot_id BIGINT NOT NULL REFERENCES snapshots(snapshot_id),
    element_id INTEGER NOT NULL REFERENCES players(element_id),
    -- Price
    now_cost INTEGER,
    cost_change_start INTEGER DEFAULT 0,
    cost_change_start_fall INTEGER DEFAULT 0,
    cost_change_event INTEGER DEFAULT 0,
    cost_change_event_fall INTEGER DEFAULT 0,
    -- Price change mechanics
    price_change_calibrating BOOLEAN DEFAULT FALSE,
    price_change_hourly_rate REAL,
    price_change_locked_until TIMESTAMPTZ,
    price_change_percent TEXT,
    price_change_projections JSONB DEFAULT '[]',
    -- Transfers
    transfers_in BIGINT DEFAULT 0,
    transfers_out BIGINT DEFAULT 0,
    transfers_in_event BIGINT DEFAULT 0,
    transfers_out_event BIGINT DEFAULT 0,
    -- Ownership
    selected_by_percent REAL,
    selected_rank INTEGER,
    -- Availability
    status TEXT DEFAULT 'a',
    chance_of_playing_this_round REAL,
    chance_of_playing_next_round REAL,
    news TEXT DEFAULT '',
    news_added TIMESTAMPTZ,
    can_select BOOLEAN DEFAULT TRUE,
    can_transact BOOLEAN DEFAULT TRUE,
    removed BOOLEAN DEFAULT FALSE,
    -- Points & performance
    total_points INTEGER DEFAULT 0,
    event_points INTEGER DEFAULT 0,
    points_per_game TEXT,
    form TEXT,
    value_form TEXT,
    value_season TEXT,
    minutes INTEGER DEFAULT 0,
    starts INTEGER DEFAULT 0,
    goals_scored INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    clean_sheets INTEGER DEFAULT 0,
    goals_conceded INTEGER DEFAULT 0,
    own_goals INTEGER DEFAULT 0,
    penalties_saved INTEGER DEFAULT 0,
    penalties_missed INTEGER DEFAULT 0,
    yellow_cards INTEGER DEFAULT 0,
    red_cards INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    bonus INTEGER DEFAULT 0,
    bps INTEGER DEFAULT 0,
    -- Expected stats
    expected_goals TEXT,
    expected_assists TEXT,
    expected_goal_involvements TEXT,
    expected_goals_conceded TEXT,
    -- ICT
    influence TEXT,
    creativity TEXT,
    threat TEXT,
    ict_index TEXT,
    -- Defensive
    clearances_blocks_interceptions INTEGER DEFAULT 0,
    defensive_contribution INTEGER DEFAULT 0,
    recoveries INTEGER DEFAULT 0,
    tackles INTEGER DEFAULT 0,
    -- Set pieces
    corners_and_indirect_freekicks_order INTEGER,
    direct_freekicks_order INTEGER,
    penalties_order INTEGER,
    -- FPL expected points
    ep_this TEXT,
    ep_next TEXT,
    -- Misc
    in_dreamteam BOOLEAN DEFAULT FALSE,
    dreamteam_count INTEGER DEFAULT 0,
    special BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (snapshot_id, element_id)
);

-- Price changes detected between consecutive snapshots
CREATE TABLE price_changes (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT REFERENCES snapshots(snapshot_id),
    element_id INTEGER REFERENCES players(element_id),
    old_cost INTEGER NOT NULL,
    new_cost INTEGER NOT NULL,
    change INTEGER NOT NULL,
    transfers_in_event BIGINT,
    transfers_out_event BIGINT,
    selected_by_percent REAL,
    price_change_percent TEXT,
    price_change_hourly_rate REAL,
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

-- Events / gameweeks
CREATE TABLE events (
    event_id INTEGER PRIMARY KEY,
    deadline_time TIMESTAMPTZ,
    is_current BOOLEAN DEFAULT FALSE,
    is_next BOOLEAN DEFAULT FALSE,
    finished BOOLEAN DEFAULT FALSE,
    average_entry_score INTEGER,
    highest_score INTEGER,
    updated_at TIMESTAMPTZ
);

-- Fixtures
CREATE TABLE fixtures (
    fixture_id INTEGER PRIMARY KEY,
    event INTEGER,
    team_h INTEGER REFERENCES teams(team_id),
    team_a INTEGER REFERENCES teams(team_id),
    team_h_score INTEGER,
    team_a_score INTEGER,
    kickoff_time TIMESTAMPTZ,
    finished BOOLEAN DEFAULT FALSE,
    team_h_difficulty INTEGER,
    team_a_difficulty INTEGER,
    stats JSONB DEFAULT '[]',
    updated_at TIMESTAMPTZ
);

-- ============================================================
-- CSV IMPORT TABLES
-- ============================================================

-- Name mapping: CSV name → element_id (additive, multiple names per player)
CREATE TABLE csv_name_mapping (
    id BIGSERIAL PRIMARY KEY,
    csv_name TEXT NOT NULL,
    csv_team TEXT NOT NULL,
    element_id INTEGER REFERENCES players(element_id),
    confidence REAL,
    source TEXT DEFAULT 'auto',  -- 'auto', 'manual', 'ai', 'confirmed'
    season TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    notes TEXT,
    UNIQUE(csv_name, csv_team, season)
);

-- Imported CSV data (one row per player per gameweek)
CREATE TABLE csv_imports (
    id BIGSERIAL PRIMARY KEY,
    season TEXT NOT NULL,
    gameweek INTEGER NOT NULL,
    element_id INTEGER REFERENCES players(element_id),
    csv_name TEXT,
    csv_team TEXT,
    position TEXT,
    bcv REAL,
    projected_sum REAL,
    csv_price REAL,
    weighted_minutes REAL,
    weighted_uppm REAL,
    ppg_longer_term REAL,
    fixture_ratio REAL,
    gw1 REAL, gw2 REAL, gw3 REAL, gw4 REAL,
    gw5 REAL, gw6 REAL, gw7 REAL, gw8 REAL,
    imported_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(season, gameweek, element_id)
);

-- ============================================================
-- EXTENSION TABLES (future data sources)
-- ============================================================

CREATE TABLE ext_model_predictions (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    element_id INTEGER REFERENCES players(element_id),
    model_version TEXT,
    prediction_type TEXT,  -- 'points', 'price_change', 'ownership'
    value REAL,
    confidence REAL
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX idx_ps_element ON player_snapshots(element_id);
CREATE INDEX idx_ps_snapshot ON player_snapshots(snapshot_id);
CREATE INDEX idx_ps_cost ON player_snapshots(now_cost);
CREATE INDEX idx_pc_element ON price_changes(element_id);
CREATE INDEX idx_pc_detected ON price_changes(detected_at);
CREATE INDEX idx_snapshots_ts ON snapshots(timestamp);
CREATE INDEX idx_players_team ON players(team_id);
CREATE INDEX idx_players_web_name ON players(web_name);
CREATE INDEX idx_csv_imports_season_gw ON csv_imports(season, gameweek);
CREATE INDEX idx_csv_mapping_lookup ON csv_name_mapping(csv_name, csv_team);

-- ============================================================
-- VIEWS
-- ============================================================

-- Latest snapshot for each player (most commonly used query)
CREATE VIEW v_player_latest AS
SELECT 
    p.element_id, p.web_name, p.first_name, p.second_name,
    p.team_id, p.element_type, p.code,
    t.name AS team_name, t.short_name AS team_short, t.code AS team_code,
    ps.now_cost, ps.cost_change_start, ps.cost_change_event,
    ps.price_change_percent, ps.price_change_hourly_rate, ps.price_change_projections,
    ps.transfers_in, ps.transfers_out, ps.transfers_in_event, ps.transfers_out_event,
    ps.selected_by_percent, ps.status, ps.news, ps.news_added,
    ps.chance_of_playing_next_round,
    ps.total_points, ps.event_points, ps.form, ps.points_per_game,
    ps.ep_this, ps.ep_next,
    ps.expected_goals, ps.expected_assists, ps.expected_goal_involvements,
    s.timestamp AS snapshot_time
FROM players p
JOIN teams t ON p.team_id = t.team_id
JOIN player_snapshots ps ON p.element_id = ps.element_id
JOIN snapshots s ON ps.snapshot_id = s.snapshot_id
WHERE s.snapshot_id = (SELECT MAX(snapshot_id) FROM snapshots);

-- Price timeline for charting
CREATE VIEW v_price_timeline AS
SELECT
    s.timestamp,
    ps.element_id,
    p.web_name,
    t.short_name AS team_short,
    ps.now_cost,
    ps.transfers_in,
    ps.transfers_out,
    ps.transfers_in_event,
    ps.transfers_out_event,
    ps.selected_by_percent,
    ps.price_change_percent,
    ps.price_change_hourly_rate,
    ps.price_change_projections
FROM player_snapshots ps
JOIN snapshots s ON ps.snapshot_id = s.snapshot_id
JOIN players p ON ps.element_id = p.element_id
JOIN teams t ON p.team_id = t.team_id
ORDER BY ps.element_id, s.timestamp;

-- ============================================================
-- ROW LEVEL SECURITY (prep for future auth)
-- ============================================================

-- For now, allow all reads. When we add auth:
-- - Free tier: can read data delayed by 1 hour
-- - Premium: real-time access + predictions
-- - Admin: full write access

ALTER TABLE snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE players ENABLE ROW LEVEL SECURITY;
ALTER TABLE player_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE fixtures ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_changes ENABLE ROW LEVEL SECURITY;
ALTER TABLE csv_imports ENABLE ROW LEVEL SECURITY;
ALTER TABLE csv_name_mapping ENABLE ROW LEVEL SECURITY;

-- Public read access for all tables (for now)
CREATE POLICY "Public read access" ON snapshots FOR SELECT USING (true);
CREATE POLICY "Public read access" ON players FOR SELECT USING (true);
CREATE POLICY "Public read access" ON player_snapshots FOR SELECT USING (true);
CREATE POLICY "Public read access" ON teams FOR SELECT USING (true);
CREATE POLICY "Public read access" ON events FOR SELECT USING (true);
CREATE POLICY "Public read access" ON fixtures FOR SELECT USING (true);
CREATE POLICY "Public read access" ON price_changes FOR SELECT USING (true);
CREATE POLICY "Public read access" ON csv_imports FOR SELECT USING (true);
CREATE POLICY "Public read access" ON csv_name_mapping FOR SELECT USING (true);

-- Service role (scraper, admin) can write everything
CREATE POLICY "Service write access" ON snapshots FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON players FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON player_snapshots FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON teams FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON events FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON fixtures FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON price_changes FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON csv_imports FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service write access" ON csv_name_mapping FOR ALL USING (true) WITH CHECK (true);
