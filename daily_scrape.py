#!/usr/bin/env python3
"""
FPL Daily Data Recorder.
Records EVERYTHING from the API. No filtering, no selection.
Raw JSON preserved + key fields in SQLite for analysis.

Run daily at 02:00 UK time.
"""
import requests, json, os, sqlite3
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "price_tracker.db")
SNAPSHOT_DIR = os.path.join(SCRIPT_DIR, "data", "daily_snapshots")
FIXTURE_DIR = os.path.join(SCRIPT_DIR, "data", "daily_fixtures")


def fetch_api(url):
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise Exception(f"API {url} returned {resp.status_code}")
    return resp.json()


def scrape():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    timestamp = now.isoformat()

    # 1. BOOTSTRAP-STATIC (all players, teams, events)
    print(f"[{timestamp}] Fetching bootstrap-static...")
    bootstrap = fetch_api("https://fantasy.premierleague.com/api/bootstrap-static/")

    # 2. FIXTURES (all matches)
    print(f"[{timestamp}] Fetching fixtures...")
    fixtures = fetch_api("https://fantasy.premierleague.com/api/fixtures/")

    # Save raw JSON — COMPLETE, UNMODIFIED
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(SNAPSHOT_DIR, f"{today}_bootstrap.json"), 'w') as f:
        json.dump(bootstrap, f)
    with open(os.path.join(FIXTURE_DIR, f"{today}_fixtures.json"), 'w') as f:
        json.dump(fixtures, f)

    # SQLite: store ALL player fields that could possibly matter for price analysis
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""CREATE TABLE IF NOT EXISTS players_daily (
        date TEXT NOT NULL,
        timestamp TEXT,
        element INTEGER NOT NULL,
        first_name TEXT,
        second_name TEXT,
        web_name TEXT,
        team INTEGER,
        team_code INTEGER,
        element_type INTEGER,
        -- PRICE
        now_cost INTEGER,
        cost_change_start INTEGER,
        cost_change_start_fall INTEGER,
        cost_change_event INTEGER,
        cost_change_event_fall INTEGER,
        -- PRICE CHANGE MECHANICS (the key fields!)
        price_change_calibrating INTEGER,
        price_change_hourly_rate REAL,
        price_change_locked_until TEXT,
        price_change_percent TEXT,
        price_change_projections TEXT,  -- JSON array
        -- TRANSFERS
        transfers_in INTEGER,
        transfers_out INTEGER,
        transfers_in_event INTEGER,
        transfers_out_event INTEGER,
        -- OWNERSHIP
        selected_by_percent REAL,
        selected_rank INTEGER,
        -- AVAILABILITY
        status TEXT,
        chance_of_playing_this_round REAL,
        chance_of_playing_next_round REAL,
        news TEXT,
        news_added TEXT,
        can_select INTEGER,
        can_transact INTEGER,
        removed INTEGER,
        -- POINTS & PERFORMANCE
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
        -- EXPECTED STATS
        expected_goals TEXT,
        expected_assists TEXT,
        expected_goal_involvements TEXT,
        expected_goals_conceded TEXT,
        -- ICT
        influence TEXT,
        creativity TEXT,
        threat TEXT,
        ict_index TEXT,
        -- DEFENSIVE
        clearances_blocks_interceptions INTEGER,
        defensive_contribution INTEGER,
        recoveries INTEGER,
        tackles INTEGER,
        -- SET PIECES
        corners_and_indirect_freekicks_order INTEGER,
        direct_freekicks_order INTEGER,
        penalties_order INTEGER,
        -- FPL EXPECTED POINTS
        ep_this TEXT,
        ep_next TEXT,
        -- MISC
        in_dreamteam INTEGER,
        dreamteam_count INTEGER,
        special INTEGER,
        PRIMARY KEY(date, timestamp, element)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS price_changes_detected (
        date TEXT NOT NULL,
        element INTEGER NOT NULL,
        web_name TEXT,
        team INTEGER,
        old_cost INTEGER,
        new_cost INTEGER,
        change INTEGER,
        transfers_in_event INTEGER,
        transfers_out_event INTEGER,
        selected_by_percent REAL,
        price_change_percent TEXT,
        price_change_hourly_rate REAL,
        PRIMARY KEY(date, element)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS events_daily (
        date TEXT NOT NULL,
        event_id INTEGER NOT NULL,
        deadline_time TEXT,
        is_current INTEGER,
        is_next INTEGER,
        finished INTEGER,
        PRIMARY KEY(date, event_id)
    )""")

    # Get previous snapshot's prices for change detection
    prev_prices = {}
    cursor = conn.execute("""
        SELECT element, now_cost FROM players_daily
        WHERE timestamp = (SELECT MAX(timestamp) FROM players_daily WHERE timestamp < ?)
    """, (timestamp,))
    for row in cursor:
        prev_prices[row[0]] = row[1]

    # Store events
    for event in bootstrap.get('events', []):
        conn.execute("INSERT OR REPLACE INTO events_daily VALUES (?,?,?,?,?,?)",
            (today, event['id'], event.get('deadline_time'),
             event.get('is_current', 0), event.get('is_next', 0), event.get('finished', 0)))

    # Store ALL player data
    changes = []
    for p in bootstrap['elements']:
        proj = json.dumps(p.get('price_change_projections', []))

        row = (
            today, timestamp, p['id'], p.get('first_name'), p.get('second_name'), p.get('web_name'),
            p.get('team'), p.get('team_code'), p.get('element_type'),
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
        placeholders = ','.join(['?'] * len(row))
        conn.execute(f"INSERT OR REPLACE INTO players_daily VALUES ({placeholders})", row)

        # Detect price change
        if p['id'] in prev_prices and prev_prices[p['id']] != p['now_cost']:
            old = prev_prices[p['id']]
            new = p['now_cost']
            conn.execute("INSERT OR REPLACE INTO price_changes_detected VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (today, p['id'], p['web_name'], p['team'], old, new, new-old,
                 p.get('transfers_in_event',0), p.get('transfers_out_event',0),
                 p.get('selected_by_percent',0), p.get('price_change_percent'),
                 p.get('price_change_hourly_rate')))
            changes.append((p['web_name'], old, new))

    conn.commit()
    conn.close()

    # Report
    print(f"[{today}] Stored {len(bootstrap['elements'])} players, {len(fixtures)} fixtures")
    if changes:
        print(f"  PRICE CHANGES DETECTED:")
        for name, old, new in changes:
            d = "↑" if new > old else "↓"
            print(f"    {d} {name}: £{old/10:.1f} → £{new/10:.1f}")
    else:
        print(f"  No price changes (vs previous snapshot)")


if __name__ == "__main__":
    scrape()
