#!/usr/bin/env python3
"""
One-off migration: push local SQLite data to Supabase.
Run with: SUPABASE_URL=xxx SUPABASE_SERVICE_KEY=xxx python3 migrate_to_supabase.py
Or trigger via GitHub Actions workflow.
"""
import sqlite3, json, os, requests, time
from urllib.parse import quote

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "fpl_tracker.db")


def supabase_post(table, data, upsert_cols=None):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    if upsert_cols:
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={upsert_cols}"
    else:
        url = f"{SUPABASE_URL}/rest/v1/{table}"

    for i in range(0, len(data), 200):
        chunk = data[i:i+200]
        resp = requests.post(url, headers=headers, json=chunk, timeout=60)
        if resp.status_code not in (200, 201, 204):
            print(f"  ERROR {table}: {resp.status_code} - {resp.text[:200]}")
            return False
    return True


def migrate():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 1. Teams
    teams = conn.execute("SELECT * FROM teams").fetchall()
    teams_data = [{"team_id": t["team_id"], "name": t["name"], "short_name": t["short_name"], "code": t["code"]} for t in teams]
    print(f"Migrating {len(teams_data)} teams...")
    supabase_post("teams", teams_data, "team_id")

    # 2. Players
    players = conn.execute("SELECT * FROM players").fetchall()
    players_data = [{
        "element_id": p["element_id"], "first_name": p["first_name"],
        "second_name": p["second_name"], "web_name": p["web_name"],
        "team_id": p["team_id"], "element_type": p["element_type"],
        "code": p["code"], "updated_at": p["updated_at"]
    } for p in players]
    print(f"Migrating {len(players_data)} players...")
    supabase_post("players", players_data, "element_id")

    # 3. Snapshots
    snapshots = conn.execute("SELECT * FROM snapshots ORDER BY snapshot_id").fetchall()
    # We need to insert these and get back the new IDs from Supabase
    # Since Supabase uses BIGSERIAL, the IDs might differ. 
    # Better: insert with specific IDs by including snapshot_id
    snapshots_data = [{
        "snapshot_id": s["snapshot_id"], "timestamp": s["timestamp"],
        "source": s["source"], "players_count": s["players_count"]
    } for s in snapshots]
    print(f"Migrating {len(snapshots_data)} snapshots...")
    supabase_post("snapshots", snapshots_data, "snapshot_id")

    # 4. Events
    events = conn.execute("SELECT * FROM events").fetchall()
    if events:
        events_data = [{
            "event_id": e["event_id"], "deadline_time": e["deadline_time"],
            "is_current": bool(e["is_current"]), "is_next": bool(e["is_next"]),
            "finished": bool(e["finished"]),
            "average_entry_score": e["average_entry_score"],
            "highest_score": e["highest_score"], "updated_at": e["updated_at"]
        } for e in events]
        print(f"Migrating {len(events_data)} events...")
        supabase_post("events", events_data, "event_id")

    # 5. Fixtures
    fixtures = conn.execute("SELECT * FROM fixtures").fetchall()
    if fixtures:
        fixtures_data = [{
            "fixture_id": f["fixture_id"], "event": f["event"],
            "team_h": f["team_h"], "team_a": f["team_a"],
            "team_h_score": f["team_h_score"], "team_a_score": f["team_a_score"],
            "kickoff_time": f["kickoff_time"], "finished": bool(f["finished"]),
            "team_h_difficulty": f["team_h_difficulty"],
            "team_a_difficulty": f["team_a_difficulty"],
            "stats": f["stats"], "updated_at": f["updated_at"]
        } for f in fixtures]
        print(f"Migrating {len(fixtures_data)} fixtures...")
        supabase_post("fixtures", fixtures_data, "fixture_id")

    # 6. Player snapshots (the big one)
    total_ps = conn.execute("SELECT COUNT(*) FROM player_snapshots").fetchone()[0]
    print(f"Migrating {total_ps} player snapshots...")

    for snap in snapshots:
        sid = snap["snapshot_id"]
        rows = conn.execute("SELECT * FROM player_snapshots WHERE snapshot_id = ?", (sid,)).fetchall()
        batch = []
        for r in rows:
            row_data = {
                "snapshot_id": r["snapshot_id"], "element_id": r["element_id"],
                "now_cost": r["now_cost"],
                "cost_change_start": r["cost_change_start"],
                "cost_change_start_fall": r["cost_change_start_fall"],
                "cost_change_event": r["cost_change_event"],
                "cost_change_event_fall": r["cost_change_event_fall"],
                "price_change_calibrating": bool(r["price_change_calibrating"]),
                "price_change_hourly_rate": r["price_change_hourly_rate"],
                "price_change_locked_until": r["price_change_locked_until"],
                "price_change_percent": r["price_change_percent"],
                "price_change_projections": json.loads(r["price_change_projections"]) if r["price_change_projections"] else [],
                "transfers_in": r["transfers_in"],
                "transfers_out": r["transfers_out"],
                "transfers_in_event": r["transfers_in_event"],
                "transfers_out_event": r["transfers_out_event"],
                "selected_by_percent": float(r["selected_by_percent"]) if r["selected_by_percent"] else 0,
                "selected_rank": r["selected_rank"],
                "status": r["status"],
                "chance_of_playing_this_round": r["chance_of_playing_this_round"],
                "chance_of_playing_next_round": r["chance_of_playing_next_round"],
                "news": r["news"], "news_added": r["news_added"],
                "can_select": bool(r["can_select"]), "can_transact": bool(r["can_transact"]),
                "removed": bool(r["removed"]),
                "total_points": r["total_points"], "event_points": r["event_points"],
                "points_per_game": r["points_per_game"], "form": r["form"],
                "value_form": r["value_form"], "value_season": r["value_season"],
                "minutes": r["minutes"], "starts": r["starts"],
                "goals_scored": r["goals_scored"], "assists": r["assists"],
                "clean_sheets": r["clean_sheets"], "goals_conceded": r["goals_conceded"],
                "own_goals": r["own_goals"], "penalties_saved": r["penalties_saved"],
                "penalties_missed": r["penalties_missed"],
                "yellow_cards": r["yellow_cards"], "red_cards": r["red_cards"],
                "saves": r["saves"], "bonus": r["bonus"], "bps": r["bps"],
                "expected_goals": r["expected_goals"],
                "expected_assists": r["expected_assists"],
                "expected_goal_involvements": r["expected_goal_involvements"],
                "expected_goals_conceded": r["expected_goals_conceded"],
                "influence": r["influence"], "creativity": r["creativity"],
                "threat": r["threat"], "ict_index": r["ict_index"],
                "clearances_blocks_interceptions": r["clearances_blocks_interceptions"],
                "defensive_contribution": r["defensive_contribution"],
                "recoveries": r["recoveries"], "tackles": r["tackles"],
                "corners_and_indirect_freekicks_order": r["corners_and_indirect_freekicks_order"],
                "direct_freekicks_order": r["direct_freekicks_order"],
                "penalties_order": r["penalties_order"],
                "ep_this": r["ep_this"], "ep_next": r["ep_next"],
                "in_dreamteam": bool(r["in_dreamteam"]),
                "dreamteam_count": r["dreamteam_count"],
                "special": bool(r["special"]),
            }
            batch.append(row_data)

        ok = supabase_post("player_snapshots", batch, "snapshot_id,element_id")
        print(f"  Snapshot #{sid}: {len(batch)} rows {'✓' if ok else '✗'}")
        time.sleep(0.5)  # Be gentle with rate limits

    # 7. Price changes
    pcs = conn.execute("SELECT * FROM price_changes").fetchall()
    if pcs:
        pc_data = [{
            "snapshot_id": p["snapshot_id"], "element_id": p["element_id"],
            "old_cost": p["old_cost"], "new_cost": p["new_cost"], "change": p["change"],
            "transfers_in_event": p["transfers_in_event"],
            "transfers_out_event": p["transfers_out_event"],
            "selected_by_percent": p["selected_by_percent"],
            "price_change_percent": p["price_change_percent"],
            "price_change_hourly_rate": p["price_change_hourly_rate"],
        } for p in pcs]
        print(f"Migrating {len(pc_data)} price changes...")
        supabase_post("price_changes", pc_data)

    # 8. CSV imports
    imports = conn.execute("SELECT * FROM csv_imports").fetchall()
    if imports:
        imports_data = [{
            "season": i["season"], "gameweek": i["gameweek"],
            "element_id": i["element_id"], "csv_name": i["csv_name"],
            "csv_team": i["csv_team"], "position": i["position"],
            "bcv": i["bcv"], "projected_sum": i["projected_sum"],
            "csv_price": i["csv_price"], "weighted_minutes": i["weighted_minutes"],
            "weighted_uppm": i["weighted_uppm"], "ppg_longer_term": i["ppg_longer_term"],
            "fixture_ratio": i["fixture_ratio"],
            "gw1": i["gw1"], "gw2": i["gw2"], "gw3": i["gw3"], "gw4": i["gw4"],
            "gw5": i["gw5"], "gw6": i["gw6"], "gw7": i["gw7"], "gw8": i["gw8"],
        } for i in imports]
        print(f"Migrating {len(imports_data)} CSV imports...")
        supabase_post("csv_imports", imports_data, "season,gameweek,element_id")

    # 9. CSV name mappings
    mappings = conn.execute("SELECT * FROM csv_name_mapping").fetchall()
    if mappings:
        map_data = [{
            "csv_name": m["csv_name"], "csv_team": m["csv_team"],
            "element_id": m["element_id"], "confidence": m["confidence"],
            "source": m["source"], "season": m["season"], "notes": m["notes"],
        } for m in mappings]
        print(f"Migrating {len(map_data)} name mappings...")
        supabase_post("csv_name_mapping", map_data, "csv_name,csv_team,season")

    print("\n✓ Migration complete!")
    conn.close()


if __name__ == "__main__":
    migrate()
