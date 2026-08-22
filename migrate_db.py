#!/usr/bin/env python3
"""
Migrate from flat players_daily table to normalized schema.
Run once. Preserves all existing data.
"""
import sqlite3, os, json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OLD_DB = os.path.join(SCRIPT_DIR, "data", "price_tracker.db")
NEW_DB = os.path.join(SCRIPT_DIR, "data", "fpl_tracker.db")

def create_schema(conn):
    conn.executescript("""
    -- Snapshot metadata
    CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL UNIQUE,
        source TEXT DEFAULT 'local',
        players_count INTEGER
    );

    -- Player master data (latest known state)
    CREATE TABLE IF NOT EXISTS players (
        element_id INTEGER PRIMARY KEY,
        first_name TEXT,
        second_name TEXT,
        web_name TEXT,
        team_id INTEGER,
        element_type INTEGER,
        code INTEGER,
        updated_at TEXT
    );

    -- Teams
    CREATE TABLE IF NOT EXISTS teams (
        team_id INTEGER PRIMARY KEY,
        name TEXT,
        short_name TEXT,
        code INTEGER
    );

    -- The main time-series table: one row per player per snapshot
    CREATE TABLE IF NOT EXISTS player_snapshots (
        snapshot_id INTEGER NOT NULL,
        element_id INTEGER NOT NULL,
        -- Price
        now_cost INTEGER,
        cost_change_start INTEGER,
        cost_change_start_fall INTEGER,
        cost_change_event INTEGER,
        cost_change_event_fall INTEGER,
        -- Price change mechanics
        price_change_calibrating INTEGER,
        price_change_hourly_rate REAL,
        price_change_locked_until TEXT,
        price_change_percent TEXT,
        price_change_projections TEXT,
        -- Transfers
        transfers_in INTEGER,
        transfers_out INTEGER,
        transfers_in_event INTEGER,
        transfers_out_event INTEGER,
        -- Ownership
        selected_by_percent REAL,
        selected_rank INTEGER,
        -- Availability
        status TEXT,
        chance_of_playing_this_round REAL,
        chance_of_playing_next_round REAL,
        news TEXT,
        news_added TEXT,
        can_select INTEGER,
        can_transact INTEGER,
        removed INTEGER,
        -- Points & performance
        total_points INTEGER,
        event_points INTEGER,
        points_per_game TEXT,
        form TEXT,
        value_form TEXT,
        value_season TEXT,
        minutes INTEGER,
        starts INTEGER,
        goals_scored INTEGER,
        assists INTEGER,
        clean_sheets INTEGER,
        goals_conceded INTEGER,
        own_goals INTEGER,
        penalties_saved INTEGER,
        penalties_missed INTEGER,
        yellow_cards INTEGER,
        red_cards INTEGER,
        saves INTEGER,
        bonus INTEGER,
        bps INTEGER,
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
        clearances_blocks_interceptions INTEGER,
        defensive_contribution INTEGER,
        recoveries INTEGER,
        tackles INTEGER,
        -- Set pieces
        corners_and_indirect_freekicks_order INTEGER,
        direct_freekicks_order INTEGER,
        penalties_order INTEGER,
        -- FPL expected points
        ep_this TEXT,
        ep_next TEXT,
        -- Misc
        in_dreamteam INTEGER,
        dreamteam_count INTEGER,
        special INTEGER,
        PRIMARY KEY (snapshot_id, element_id),
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
        FOREIGN KEY (element_id) REFERENCES players(element_id)
    );

    -- Price changes detected
    CREATE TABLE IF NOT EXISTS price_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER,
        element_id INTEGER,
        old_cost INTEGER,
        new_cost INTEGER,
        change INTEGER,
        transfers_in_event INTEGER,
        transfers_out_event INTEGER,
        selected_by_percent REAL,
        price_change_percent TEXT,
        price_change_hourly_rate REAL,
        FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
        FOREIGN KEY (element_id) REFERENCES players(element_id)
    );

    -- Events/gameweeks
    CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY,
        deadline_time TEXT,
        is_current INTEGER,
        is_next INTEGER,
        finished INTEGER,
        average_entry_score INTEGER,
        highest_score INTEGER,
        updated_at TEXT
    );

    -- Fixtures
    CREATE TABLE IF NOT EXISTS fixtures (
        fixture_id INTEGER PRIMARY KEY,
        event INTEGER,
        team_h INTEGER,
        team_a INTEGER,
        team_h_score INTEGER,
        team_a_score INTEGER,
        kickoff_time TEXT,
        finished INTEGER,
        team_h_difficulty INTEGER,
        team_a_difficulty INTEGER,
        stats TEXT,
        updated_at TEXT
    );

    -- Extension tables (ready for future use)
    CREATE TABLE IF NOT EXISTS ext_creator_picks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        season TEXT,
        gameweek INTEGER,
        element_id INTEGER,
        bcv REAL,
        projected_sum REAL,
        csv_price REAL,
        recommended_action TEXT,
        FOREIGN KEY (element_id) REFERENCES players(element_id)
    );

    CREATE TABLE IF NOT EXISTS ext_model_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        element_id INTEGER,
        model_version TEXT,
        prediction_type TEXT,
        value REAL,
        confidence REAL,
        FOREIGN KEY (element_id) REFERENCES players(element_id)
    );

    -- Indexes for common queries
    CREATE INDEX IF NOT EXISTS idx_ps_element ON player_snapshots(element_id);
    CREATE INDEX IF NOT EXISTS idx_ps_snapshot ON player_snapshots(snapshot_id);
    CREATE INDEX IF NOT EXISTS idx_pc_element ON price_changes(element_id);
    CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(timestamp);

    -- Views
    CREATE VIEW IF NOT EXISTS v_player_latest AS
    SELECT p.*, ps.*,
           s.timestamp as snapshot_time
    FROM players p
    JOIN player_snapshots ps ON p.element_id = ps.element_id
    JOIN snapshots s ON ps.snapshot_id = s.snapshot_id
    WHERE s.snapshot_id = (SELECT MAX(snapshot_id) FROM snapshots);

    CREATE VIEW IF NOT EXISTS v_price_timeline AS
    SELECT s.timestamp, p.web_name, p.team_id,
           ps.now_cost, ps.transfers_in, ps.transfers_out,
           ps.transfers_in_event, ps.transfers_out_event,
           ps.selected_by_percent,
           ps.price_change_percent, ps.price_change_hourly_rate,
           ps.price_change_projections
    FROM player_snapshots ps
    JOIN snapshots s ON ps.snapshot_id = s.snapshot_id
    JOIN players p ON ps.element_id = p.element_id
    ORDER BY p.element_id, s.timestamp;
    """)


def migrate_old_data(new_conn):
    """Migrate data from old price_tracker.db if it exists."""
    if not os.path.exists(OLD_DB):
        print("No old database to migrate.")
        return

    old_conn = sqlite3.connect(OLD_DB)
    old_conn.row_factory = sqlite3.Row

    # Check if there's data
    try:
        rows = old_conn.execute("SELECT * FROM players_daily ORDER BY timestamp").fetchall()
    except:
        print("No players_daily table in old DB.")
        return

    if not rows:
        print("No data to migrate.")
        return

    print(f"Migrating {len(rows)} rows from old database...")

    # Group by timestamp
    from collections import defaultdict
    by_timestamp = defaultdict(list)
    for row in rows:
        by_timestamp[row['timestamp']].append(row)

    for ts, players in sorted(by_timestamp.items()):
        # Create snapshot
        new_conn.execute("INSERT OR IGNORE INTO snapshots (timestamp, source, players_count) VALUES (?, 'local', ?)",
                        (ts, len(players)))
        snap_id = new_conn.execute("SELECT snapshot_id FROM snapshots WHERE timestamp = ?", (ts,)).fetchone()[0]

        for p in players:
            # Update player master
            new_conn.execute("""INSERT OR REPLACE INTO players
                (element_id, first_name, second_name, web_name, team_id, element_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (p['element'], p['first_name'], p['second_name'], p['web_name'],
                 p['team'], p['element_type'], ts))

            # Insert snapshot data
            vals = (snap_id, p['element'],
                 p['now_cost'], p['cost_change_start'], p['cost_change_start_fall'],
                 p['cost_change_event'], p['cost_change_event_fall'],
                 p['price_change_calibrating'], p['price_change_hourly_rate'],
                 p['price_change_locked_until'], p['price_change_percent'],
                 p['price_change_projections'],
                 p['transfers_in'], p['transfers_out'],
                 p['transfers_in_event'], p['transfers_out_event'],
                 p['selected_by_percent'], p['selected_rank'],
                 p['status'], p['chance_of_playing_this_round'],
                 p['chance_of_playing_next_round'], p['news'], p['news_added'],
                 p['can_select'], p['can_transact'], p['removed'],
                 p['total_points'], p['event_points'], p['points_per_game'],
                 p['form'], p['value_form'], p['value_season'],
                 p['minutes'], p['starts'], p['goals_scored'], p['assists'],
                 p['clean_sheets'], p['goals_conceded'],
                 p['own_goals'], p['penalties_saved'], p['penalties_missed'],
                 p['yellow_cards'], p['red_cards'], p['saves'],
                 p['bonus'], p['bps'],
                 p['expected_goals'], p['expected_assists'],
                 p['expected_goal_involvements'], p['expected_goals_conceded'],
                 p['influence'], p['creativity'], p['threat'], p['ict_index'],
                 p['clearances_blocks_interceptions'], p['defensive_contribution'],
                 p['recoveries'], p['tackles'],
                 p['corners_and_indirect_freekicks_order'], p['direct_freekicks_order'],
                 p['penalties_order'],
                 p['ep_this'], p['ep_next'],
                 p['in_dreamteam'], p['dreamteam_count'], p['special'])
            placeholders = ','.join(['?'] * len(vals))
            new_conn.execute(f"INSERT OR REPLACE INTO player_snapshots VALUES ({placeholders})", vals)

    new_conn.commit()
    print(f"Migrated {len(by_timestamp)} snapshots, {len(rows)} player records.")


if __name__ == "__main__":
    print(f"Creating new database: {NEW_DB}")
    conn = sqlite3.connect(NEW_DB)
    create_schema(conn)
    migrate_old_data(conn)

    # Verify
    c = conn.execute("SELECT COUNT(*) FROM snapshots")
    print(f"\nVerification:")
    print(f"  Snapshots: {c.fetchone()[0]}")
    c = conn.execute("SELECT COUNT(*) FROM players")
    print(f"  Players: {c.fetchone()[0]}")
    c = conn.execute("SELECT COUNT(*) FROM player_snapshots")
    print(f"  Player snapshot rows: {c.fetchone()[0]}")
    conn.close()
    print("\nDone. New database ready at data/fpl_tracker.db")
