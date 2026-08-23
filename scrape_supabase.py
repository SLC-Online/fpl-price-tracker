#!/usr/bin/env python3
"""
FPL Hourly Data Recorder — Supabase version.
Records EVERYTHING from the API into Supabase Postgres.

Requires: SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables.
Falls back to local SQLite if Supabase credentials not set.

Runs hourly via GitHub Actions.
"""
import requests, json, os, time
from datetime import datetime, timezone
from urllib.parse import quote

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
FPL_BASE = "https://fantasy.premierleague.com/api"


def fetch_fpl(url, retries=3):
    """Fetch FPL API with retry logic."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 503 and attempt < retries - 1:
                time.sleep(30)
                continue
            raise Exception(f"FPL API {url} returned {resp.status_code}")
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(10)
                continue
            raise
    raise Exception(f"FPL API {url} failed after {retries} attempts")


def supabase_post(table, data, upsert_cols=None):
    """Insert/upsert data into Supabase."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    if upsert_cols:
        headers["Prefer"] = f"resolution=merge-duplicates,return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={upsert_cols}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"

    # Batch in chunks of 500
    for i in range(0, len(data), 500):
        chunk = data[i:i+500]
        resp = requests.post(url, headers=headers, json=chunk, timeout=30)
        if resp.status_code not in (200, 201, 204):
            raise Exception(f"Supabase {table} error {resp.status_code}: {resp.text[:200]}")


def supabase_get(table, params=""):
    """Query Supabase."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    url = f"{SUPABASE_URL}/rest/v1/{table}?{params}"
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        raise Exception(f"Supabase GET {table} error: {resp.status_code}")
    return resp.json()


def scrape():
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat()
    source = os.environ.get("SCRAPE_SOURCE", "local")

    # 1. Fetch FPL data
    print(f"[{timestamp}] Fetching bootstrap-static...")
    bootstrap = fetch_fpl(f"{FPL_BASE}/bootstrap-static/")

    print(f"[{timestamp}] Fetching fixtures...")
    fixtures = fetch_fpl(f"{FPL_BASE}/fixtures/")

    # 2. Save raw JSON locally (if running locally)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    snapshot_dir = os.path.join(script_dir, "data", "daily_snapshots")
    fixture_dir = os.path.join(script_dir, "data", "daily_fixtures")
    os.makedirs(snapshot_dir, exist_ok=True)
    os.makedirs(fixture_dir, exist_ok=True)
    today = now.strftime("%Y-%m-%d")
    with open(os.path.join(snapshot_dir, f"{today}_bootstrap.json"), 'w') as f:
        json.dump(bootstrap, f)
    with open(os.path.join(fixture_dir, f"{today}_fixtures.json"), 'w') as f:
        json.dump(fixtures, f)

    # 3. Write to Supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  [WARN] No Supabase credentials — falling back to local-only mode")
        # Could call the old SQLite scraper here
        return

    # Create snapshot
    snapshot_data = [{"timestamp": timestamp, "source": source, "players_count": len(bootstrap['elements'])}]
    supabase_post("snapshots", snapshot_data)

    # Get the snapshot_id we just created
    encoded_ts = quote(timestamp, safe='')
    snaps = supabase_get("snapshots", f"timestamp=eq.{encoded_ts}&select=snapshot_id")
    if not snaps:
        raise Exception("Failed to retrieve snapshot_id after insert")
    snapshot_id = snaps[0]['snapshot_id']

    # Get previous snapshot's prices for change detection
    prev_snap = supabase_get("snapshots",
        f"snapshot_id=lt.{snapshot_id}&select=snapshot_id&order=snapshot_id.desc&limit=1")
    prev_prices = {}
    if prev_snap:
        prev_ps = supabase_get("player_snapshots",
            f"snapshot_id=eq.{prev_snap[0]['snapshot_id']}&select=element_id,now_cost")
        prev_prices = {p['element_id']: p['now_cost'] for p in prev_ps}

    # Upsert teams
    teams_data = [{
        "team_id": t['id'], "name": t['name'],
        "short_name": t['short_name'], "code": t['code']
    } for t in bootstrap.get('teams', [])]
    supabase_post("teams", teams_data, upsert_cols="team_id")

    # Upsert events
    events_data = [{
        "event_id": e['id'], "deadline_time": e.get('deadline_time'),
        "is_current": e.get('is_current', False), "is_next": e.get('is_next', False),
        "finished": e.get('finished', False),
        "average_entry_score": e.get('average_entry_score'),
        "highest_score": e.get('highest_score'),
        "updated_at": timestamp
    } for e in bootstrap.get('events', [])]
    supabase_post("events", events_data, upsert_cols="event_id")

    # Upsert fixtures
    fixtures_data = [{
        "fixture_id": fx['id'], "event": fx.get('event'),
        "team_h": fx.get('team_h'), "team_a": fx.get('team_a'),
        "team_h_score": fx.get('team_h_score'), "team_a_score": fx.get('team_a_score'),
        "kickoff_time": fx.get('kickoff_time'),
        "finished": fx.get('finished', False),
        "team_h_difficulty": fx.get('team_h_difficulty'),
        "team_a_difficulty": fx.get('team_a_difficulty'),
        "stats": json.dumps(fx.get('stats', [])),
        "updated_at": timestamp
    } for fx in fixtures]
    supabase_post("fixtures", fixtures_data, upsert_cols="fixture_id")

    # Upsert players + insert snapshots
    players_data = []
    snapshots_data = []
    changes = []

    for p in bootstrap['elements']:
        # Player master record
        players_data.append({
            "element_id": p['id'], "first_name": p.get('first_name'),
            "second_name": p.get('second_name'), "web_name": p.get('web_name'),
            "team_id": p.get('team'), "element_type": p.get('element_type'),
            "code": p.get('code'), "updated_at": timestamp
        })

        # Snapshot data
        snapshots_data.append({
            "snapshot_id": snapshot_id, "element_id": p['id'],
            "now_cost": p.get('now_cost'),
            "cost_change_start": p.get('cost_change_start', 0),
            "cost_change_start_fall": p.get('cost_change_start_fall', 0),
            "cost_change_event": p.get('cost_change_event', 0),
            "cost_change_event_fall": p.get('cost_change_event_fall', 0),
            "price_change_calibrating": bool(p.get('price_change_calibrating')),
            "price_change_hourly_rate": p.get('price_change_hourly_rate'),
            "price_change_locked_until": p.get('price_change_locked_until'),
            "price_change_percent": p.get('price_change_percent'),
            "price_change_projections": p.get('price_change_projections', []),
            "transfers_in": p.get('transfers_in', 0),
            "transfers_out": p.get('transfers_out', 0),
            "transfers_in_event": p.get('transfers_in_event', 0),
            "transfers_out_event": p.get('transfers_out_event', 0),
            "selected_by_percent": float(p.get('selected_by_percent', 0)),
            "selected_rank": p.get('selected_rank'),
            "status": p.get('status', 'a'),
            "chance_of_playing_this_round": p.get('chance_of_playing_this_round'),
            "chance_of_playing_next_round": p.get('chance_of_playing_next_round'),
            "news": p.get('news', ''),
            "news_added": p.get('news_added'),
            "can_select": bool(p.get('can_select')),
            "can_transact": bool(p.get('can_transact')),
            "removed": bool(p.get('removed')),
            "total_points": p.get('total_points', 0),
            "event_points": p.get('event_points', 0),
            "points_per_game": p.get('points_per_game'),
            "form": p.get('form'),
            "value_form": p.get('value_form'),
            "value_season": p.get('value_season'),
            "minutes": p.get('minutes', 0),
            "starts": p.get('starts', 0),
            "goals_scored": p.get('goals_scored', 0),
            "assists": p.get('assists', 0),
            "clean_sheets": p.get('clean_sheets', 0),
            "goals_conceded": p.get('goals_conceded', 0),
            "own_goals": p.get('own_goals', 0),
            "penalties_saved": p.get('penalties_saved', 0),
            "penalties_missed": p.get('penalties_missed', 0),
            "yellow_cards": p.get('yellow_cards', 0),
            "red_cards": p.get('red_cards', 0),
            "saves": p.get('saves', 0),
            "bonus": p.get('bonus', 0),
            "bps": p.get('bps', 0),
            "expected_goals": p.get('expected_goals'),
            "expected_assists": p.get('expected_assists'),
            "expected_goal_involvements": p.get('expected_goal_involvements'),
            "expected_goals_conceded": p.get('expected_goals_conceded'),
            "influence": p.get('influence'),
            "creativity": p.get('creativity'),
            "threat": p.get('threat'),
            "ict_index": p.get('ict_index'),
            "clearances_blocks_interceptions": p.get('clearances_blocks_interceptions', 0),
            "defensive_contribution": p.get('defensive_contribution', 0),
            "recoveries": p.get('recoveries', 0),
            "tackles": p.get('tackles', 0),
            "corners_and_indirect_freekicks_order": p.get('corners_and_indirect_freekicks_order'),
            "direct_freekicks_order": p.get('direct_freekicks_order'),
            "penalties_order": p.get('penalties_order'),
            "ep_this": p.get('ep_this'),
            "ep_next": p.get('ep_next'),
            "in_dreamteam": bool(p.get('in_dreamteam')),
            "dreamteam_count": p.get('dreamteam_count', 0),
            "special": bool(p.get('special')),
        })

        # Detect price change
        if p['id'] in prev_prices and prev_prices[p['id']] != p.get('now_cost'):
            old = prev_prices[p['id']]
            new = p['now_cost']
            changes.append({
                "snapshot_id": snapshot_id, "element_id": p['id'],
                "old_cost": old, "new_cost": new, "change": new - old,
                "transfers_in_event": p.get('transfers_in_event', 0),
                "transfers_out_event": p.get('transfers_out_event', 0),
                "selected_by_percent": float(p.get('selected_by_percent', 0)),
                "price_change_percent": p.get('price_change_percent'),
                "price_change_hourly_rate": p.get('price_change_hourly_rate'),
            })

    # Write to Supabase
    print(f"  Writing {len(players_data)} players...")
    supabase_post("players", players_data, upsert_cols="element_id")

    print(f"  Writing {len(snapshots_data)} player snapshots...")
    supabase_post("player_snapshots", snapshots_data, upsert_cols="snapshot_id,element_id")

    if changes:
        print(f"  Writing {len(changes)} price changes...")
        supabase_post("price_changes", changes)

    # Report
    print(f"[{today}] Snapshot #{snapshot_id}: {len(bootstrap['elements'])} players, "
          f"{len(fixtures)} fixtures, {len(bootstrap.get('teams', []))} teams")
    if changes:
        print(f"  PRICE CHANGES DETECTED:")
        for c in changes:
            name = next((p['web_name'] for p in bootstrap['elements'] if p['id'] == c['element_id']), '?')
            d = "↑" if c['change'] > 0 else "↓"
            print(f"    {d} {name}: £{c['old_cost']/10:.1f} → £{c['new_cost']/10:.1f}")
    else:
        print(f"  No price changes (vs previous snapshot)")


if __name__ == "__main__":
    scrape()
