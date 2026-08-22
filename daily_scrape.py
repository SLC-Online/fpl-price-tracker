#!/usr/bin/env python3
"""
FPL Hourly Data Recorder.
Records EVERYTHING from the API into normalized schema.
Raw JSON preserved locally. SQLite DB committed to GitHub.

Runs hourly via cron (local) + GitHub Actions (cloud).
"""
import requests, json, os, sqlite3, time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "fpl_tracker.db")
SNAPSHOT_DIR = os.path.join(SCRIPT_DIR, "data", "daily_snapshots")
FIXTURE_DIR = os.path.join(SCRIPT_DIR, "data", "daily_fixtures")

FPL_BASE = "https://fantasy.premierleague.com/api"


def fetch_api(url, retries=3):
    """Fetch with retry logic for resilience."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 503 and attempt < retries - 1:
                time.sleep(30)
                continue
            raise Exception(f"API {url} returned {resp.status_code}")
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(10)
                continue
            raise
    raise Exception(f"API {url} failed after {retries} attempts")


def ensure_schema(conn):
    """Create tables if they don't exist."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS snapshots (
        snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL UNIQUE,
        source TEXT DEFAULT 'local',
        players_count INTEGER
    );

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

    CREATE TABLE IF NOT EXISTS teams (
        team_id INTEGER PRIMARY KEY,
        name TEXT,
        short_name TEXT,
        code INTEGER
    );

    CREATE TABLE IF NOT EXISTS player_snapshots (
        snapshot_id INTEGER NOT NULL,
        element_id INTEGER NOT NULL,
        now_cost INTEGER,
        cost_change_start INTEGER,
        cost_change_start_fall INTEGER,
        cost_change_event INTEGER,
        cost_change_event_fall INTEGER,
        price_change_calibrating INTEGER,
        price_change_hourly_rate REAL,
        price_change_locked_until TEXT,
        price_change_percent TEXT,
        price_change_projections TEXT,
        transfers_in INTEGER,
        transfers_out INTEGER,
        transfers_in_event INTEGER,
        transfers_out_event INTEGER,
        selected_by_percent REAL,
        selected_rank INTEGER,
        status TEXT,
        chance_of_playing_this_round REAL,
        chance_of_playing_next_round REAL,
        news TEXT,
        news_added TEXT,
        can_select INTEGER,
        can_transact INTEGER,
        removed INTEGER,
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
        expected_goals TEXT,
        expected_assists TEXT,
        expected_goal_involvements TEXT,
        expected_goals_conceded TEXT,
        influence TEXT,
        creativity TEXT,
        threat TEXT,
        ict_index TEXT,
        clearances_blocks_interceptions INTEGER,
        defensive_contribution INTEGER,
        recoveries INTEGER,
        tackles INTEGER,
        corners_and_indirect_freekicks_order INTEGER,
        direct_freekicks_order INTEGER,
        penalties_order INTEGER,
        ep_this TEXT,
        ep_next TEXT,
        in_dreamteam INTEGER,
        dreamteam_count INTEGER,
        special INTEGER,
        PRIMARY KEY (snapshot_id, element_id)
    );

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
        price_change_hourly_rate REAL
    );

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

    CREATE INDEX IF NOT EXISTS idx_ps_element ON player_snapshots(element_id);
    CREATE INDEX IF NOT EXISTS idx_ps_snapshot ON player_snapshots(snapshot_id);
    CREATE INDEX IF NOT EXISTS idx_pc_element ON price_changes(element_id);
    CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(timestamp);
    """)


def scrape():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    timestamp = now.isoformat()
    source = os.environ.get("SCRAPE_SOURCE", "local")

    # 1. Fetch data
    print(f"[{timestamp}] Fetching bootstrap-static...")
    bootstrap = fetch_api(f"{FPL_BASE}/bootstrap-static/")

    print(f"[{timestamp}] Fetching fixtures...")
    fixtures = fetch_api(f"{FPL_BASE}/fixtures/")

    # 2. Save raw JSON locally (overwritten each hour — DB is the real record)
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(SNAPSHOT_DIR, f"{today}_bootstrap.json"), 'w') as f:
        json.dump(bootstrap, f)
    with open(os.path.join(FIXTURE_DIR, f"{today}_fixtures.json"), 'w') as f:
        json.dump(fixtures, f)

    # 3. Write to normalized SQLite
    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)

    # Create snapshot record
    conn.execute("INSERT INTO snapshots (timestamp, source, players_count) VALUES (?, ?, ?)",
                 (timestamp, source, len(bootstrap['elements'])))
    snap_id = conn.execute("SELECT snapshot_id FROM snapshots WHERE timestamp = ?",
                           (timestamp,)).fetchone()[0]

    # Get previous prices for change detection
    prev_prices = {}
    prev_snap = conn.execute(
        "SELECT snapshot_id FROM snapshots WHERE snapshot_id < ? ORDER BY snapshot_id DESC LIMIT 1",
        (snap_id,)).fetchone()
    if prev_snap:
        cursor = conn.execute(
            "SELECT element_id, now_cost FROM player_snapshots WHERE snapshot_id = ?",
            (prev_snap[0],))
        prev_prices = {r[0]: r[1] for r in cursor}

    # Store teams
    for t in bootstrap.get('teams', []):
        conn.execute("INSERT OR REPLACE INTO teams VALUES (?, ?, ?, ?)",
                     (t['id'], t.get('name'), t.get('short_name'), t.get('code')))

    # Store events
    for event in bootstrap.get('events', []):
        conn.execute("""INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (event['id'], event.get('deadline_time'),
                      event.get('is_current', 0), event.get('is_next', 0),
                      event.get('finished', 0), event.get('average_entry_score'),
                      event.get('highest_score'), timestamp))

    # Store fixtures
    for fx in fixtures:
        conn.execute("""INSERT OR REPLACE INTO fixtures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (fx['id'], fx.get('event'), fx.get('team_h'), fx.get('team_a'),
                      fx.get('team_h_score'), fx.get('team_a_score'),
                      fx.get('kickoff_time'), fx.get('finished', 0),
                      fx.get('team_h_difficulty'), fx.get('team_a_difficulty'),
                      json.dumps(fx.get('stats', [])), timestamp))

    # Store all player data
    changes = []
    for p in bootstrap['elements']:
        proj = json.dumps(p.get('price_change_projections', []))

        # Update player master record
        conn.execute("""INSERT OR REPLACE INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (p['id'], p.get('first_name'), p.get('second_name'), p.get('web_name'),
                      p.get('team'), p.get('element_type'), p.get('code'), timestamp))

        # Insert snapshot
        vals = (
            snap_id, p['id'],
            p.get('now_cost'), p.get('cost_change_start', 0), p.get('cost_change_start_fall', 0),
            p.get('cost_change_event', 0), p.get('cost_change_event_fall', 0),
            1 if p.get('price_change_calibrating') else 0,
            p.get('price_change_hourly_rate'), p.get('price_change_locked_until'),
            p.get('price_change_percent'), proj,
            p.get('transfers_in', 0), p.get('transfers_out', 0),
            p.get('transfers_in_event', 0), p.get('transfers_out_event', 0),
            p.get('selected_by_percent', 0), p.get('selected_rank'),
            p.get('status', 'a'), p.get('chance_of_playing_this_round'),
            p.get('chance_of_playing_next_round'), p.get('news', ''), p.get('news_added'),
            1 if p.get('can_select') else 0, 1 if p.get('can_transact') else 0,
            1 if p.get('removed') else 0,
            p.get('total_points', 0), p.get('event_points', 0), p.get('points_per_game'),
            p.get('form'), p.get('value_form'), p.get('value_season'),
            p.get('minutes', 0), p.get('starts', 0), p.get('goals_scored', 0),
            p.get('assists', 0), p.get('clean_sheets', 0), p.get('goals_conceded', 0),
            p.get('own_goals', 0), p.get('penalties_saved', 0), p.get('penalties_missed', 0),
            p.get('yellow_cards', 0), p.get('red_cards', 0), p.get('saves', 0),
            p.get('bonus', 0), p.get('bps', 0),
            p.get('expected_goals'), p.get('expected_assists'),
            p.get('expected_goal_involvements'), p.get('expected_goals_conceded'),
            p.get('influence'), p.get('creativity'), p.get('threat'), p.get('ict_index'),
            p.get('clearances_blocks_interceptions', 0), p.get('defensive_contribution', 0),
            p.get('recoveries', 0), p.get('tackles', 0),
            p.get('corners_and_indirect_freekicks_order'), p.get('direct_freekicks_order'),
            p.get('penalties_order'),
            p.get('ep_this'), p.get('ep_next'),
            1 if p.get('in_dreamteam') else 0, p.get('dreamteam_count', 0),
            1 if p.get('special') else 0,
        )
        placeholders = ','.join(['?'] * len(vals))
        conn.execute(f"INSERT OR REPLACE INTO player_snapshots VALUES ({placeholders})", vals)

        # Detect price change
        if p['id'] in prev_prices and prev_prices[p['id']] != p.get('now_cost'):
            old = prev_prices[p['id']]
            new = p['now_cost']
            conn.execute("""INSERT INTO price_changes
                (snapshot_id, element_id, old_cost, new_cost, change,
                 transfers_in_event, transfers_out_event, selected_by_percent,
                 price_change_percent, price_change_hourly_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snap_id, p['id'], old, new, new - old,
                 p.get('transfers_in_event', 0), p.get('transfers_out_event', 0),
                 p.get('selected_by_percent', 0), p.get('price_change_percent'),
                 p.get('price_change_hourly_rate')))
            changes.append((p['web_name'], old, new))

    conn.commit()
    conn.close()

    # Report
    print(f"[{today}] Snapshot #{snap_id}: {len(bootstrap['elements'])} players, "
          f"{len(fixtures)} fixtures, {len(bootstrap.get('teams', []))} teams")
    if changes:
        print(f"  PRICE CHANGES DETECTED:")
        for name, old, new in changes:
            d = "↑" if new > old else "↓"
            print(f"    {d} {name}: £{old/10:.1f} → £{new/10:.1f}")
    else:
        print(f"  No price changes (vs previous snapshot)")


if __name__ == "__main__":
    scrape()
