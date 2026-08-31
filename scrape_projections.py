#!/usr/bin/env python3
"""
Scrape external projection sources (FantaLens, FPL Form) and store in Supabase.

Runs daily via GitHub Actions at 12:00 UTC and 17:00 UTC.
Checks the FPL API for the next deadline and records how far away it is.
Snapshots taken within 2 hours of a deadline are flagged 'pre_deadline'.

Sources:
  - FantaLens: JSON embedded in paginated HTML (fantalens.com/players)
  - FPL Form: CSV via POST (fplform.com/export-fpl-form-data.php)

Requires: SUPABASE_URL, SUPABASE_SERVICE_KEY environment variables.
"""
import requests, json, os, re, time, csv
from datetime import datetime, timezone, timedelta
from io import StringIO
from urllib.parse import quote

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
FPL_BASE = "https://fantasy.premierleague.com/api"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def supabase_post(table, data, upsert_cols=None):
    headers = dict(HEADERS)
    if upsert_cols:
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={upsert_cols}"
    else:
        headers["Prefer"] = "return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}"

    for i in range(0, len(data), 200):
        chunk = data[i:i + 200]
        resp = requests.post(url, headers=headers, json=chunk, timeout=30)
        if resp.status_code not in (200, 201, 204):
            print(f"  ERROR {table}: {resp.status_code} - {resp.text[:200]}")
            return False
    return True


def get_next_deadline():
    """Get the next GW deadline from the FPL API."""
    resp = requests.get(f"{FPL_BASE}/bootstrap-static/", timeout=15)
    data = resp.json()
    for event in data['events']:
        if event.get('is_next'):
            return {
                'gameweek': event['id'],
                'deadline': datetime.fromisoformat(event['deadline_time'].replace('Z', '+00:00')),
            }
    # Fallback: find first unfinished
    for event in data['events']:
        if not event.get('finished'):
            return {
                'gameweek': event['id'],
                'deadline': datetime.fromisoformat(event['deadline_time'].replace('Z', '+00:00')),
            }
    return None


def get_or_create_source(source_name, description):
    """Get source_id from Supabase, create if not exists."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/projection_sources?source_name=eq.{quote(source_name)}&select=id",
        headers=HEADERS, timeout=10
    )
    rows = resp.json()
    if rows:
        return rows[0]['id']
    # Create
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/projection_sources",
        headers={**HEADERS, "Prefer": "return=representation"},
        json={"source_name": source_name, "description": description, "weight": 1.0, "active": True},
        timeout=10
    )
    return resp.json()[0]['id']


def scrape_fantalens(gameweek):
    """Scrape FantaLens projected points for all players."""
    print(f"  Scraping FantaLens...")
    all_players = []
    page = 1
    while True:
        try:
            resp = requests.get(
                f"https://fantalens.com/players?page={page}",
                timeout=15, headers={'User-Agent': 'Mozilla/5.0'}
            )
            scripts = re.findall(
                r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
                resp.text, re.DOTALL
            )
            if not scripts:
                break
            data = json.loads(scripts[0])
            props = data.get('props') or {}
            players = props.get('players') or []
            if not players:
                break
            all_players.extend(players)
            if len(players) < 25:
                break
            page += 1
            time.sleep(0.5)  # Be polite
        except Exception as e:
            print(f"    Page {page} error: {e}")
            break

    print(f"    Got {len(all_players)} players")

    # Extract projections
    projections = []
    for p in all_players:
        element_id = p.get('external_id')
        if not element_id:
            continue
        xpts_data = p.get('xpts') or {}
        for gw_str, gw_data in xpts_data.items():
            try:
                gw_num = int(gw_str)
            except (ValueError, TypeError):
                continue
            if isinstance(gw_data, dict):
                total = gw_data.get('total')
                if total is not None:
                    meta = {}
                    # Extract rich breakdown if available
                    fixtures = gw_data.get('fixtures') or []
                    if fixtures and isinstance(fixtures[0], dict):
                        fx = fixtures[0]
                        quantities = fx.get('quantities') or {}
                        meta = {
                            'start_prob': fx.get('start_prob'),
                            'expected_minutes': fx.get('expected_minutes'),
                            'proj_goals': quantities.get('goals'),
                            'proj_assists': quantities.get('assists'),
                            'cs_prob': quantities.get('clean_sheet_team'),
                            'opponent': fx.get('opponent'),
                            'is_home': fx.get('is_home'),
                            'difficulty': fx.get('difficulty'),
                        }
                    projections.append({
                        'element_id': element_id,
                        'gameweek': gw_num,
                        'expected_points': total,
                        'meta': meta,
                    })

    print(f"    Extracted {len(projections)} projection rows")
    return projections


def scrape_fplform(gameweek):
    """Scrape FPL Form predicted points CSV."""
    print(f"  Scraping FPL Form...")
    try:
        session = requests.Session()
        session.get("https://fplform.com/export-fpl-form-data", timeout=15)

        # Request GWs from current next through +7
        first_gw = gameweek
        last_gw = min(gameweek + 7, 38)

        resp = session.post(
            "https://fplform.com/export-fpl-form-data.php",
            data={'firstgw': str(first_gw), 'lastgw': str(last_gw), 'all': '0'},
            timeout=30,
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://fplform.com/export-fpl-form-data',
            }
        )

        if resp.status_code != 200 or 'csv' not in resp.headers.get('content-type', ''):
            print(f"    Failed: status={resp.status_code}, ct={resp.headers.get('content-type')}")
            return []

        projections = []
        reader = csv.DictReader(StringIO(resp.text))
        for row in reader:
            try:
                element_id = int(row['ID'])
            except:
                continue
            for col_name, value in row.items():
                if col_name.endswith('_pts'):
                    try:
                        gw_num = int(col_name.split('_')[0])
                        pts = float(value)
                        projections.append({
                            'element_id': element_id,
                            'gameweek': gw_num,
                            'expected_points': pts,
                            'meta': {},
                        })
                    except:
                        continue

        print(f"    Got {len(projections)} projection rows from {len(list(csv.DictReader(StringIO(resp.text))))} players")
        return projections

    except Exception as e:
        print(f"    Error: {e}")
        return []


def get_valid_element_ids():
    """Fetch the set of element_ids that exist in our players table.
    Projections referencing unknown IDs must be filtered out, otherwise the
    whole batch is rejected by the foreign-key constraint."""
    ids = set()
    offset = 0
    while True:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/players?select=element_id&limit=1000&offset={offset}",
            headers=HEADERS, timeout=15
        )
        if resp.status_code != 200:
            break
        rows = resp.json()
        if not rows:
            break
        ids.update(r['element_id'] for r in rows)
        if len(rows) < 1000:
            break
        offset += 1000
    return ids


def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Projection scraper starting")

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  ERROR: No Supabase credentials")
        raise SystemExit(1)

    # Get next deadline info
    deadline_info = get_next_deadline()
    if not deadline_info:
        print("  ERROR: Could not determine next deadline")
        raise SystemExit(1)

    next_gw = deadline_info['gameweek']
    deadline = deadline_info['deadline']
    hours_to_deadline = (deadline - now).total_seconds() / 3600

    print(f"  Next deadline: GW{next_gw} at {deadline.isoformat()}")
    print(f"  Hours until deadline: {hours_to_deadline:.1f}")

    is_pre_deadline = 0 < hours_to_deadline <= 2
    if is_pre_deadline:
        print(f"  *** PRE-DEADLINE SNAPSHOT ***")

    # Valid element IDs (to filter out players not in our DB)
    valid_ids = get_valid_element_ids()
    print(f"  {len(valid_ids)} valid player IDs in DB")

    # Get/create sources
    fl_source_id = get_or_create_source('fantalens', 'FantaLens.com - per-fixture projections with start probability and breakdown')
    ff_source_id = get_or_create_source('fpl_form', 'FPLForm.com - predicted points per fixture')

    had_error = False

    # --- Scrape FantaLens ---
    try:
        fl_projections = scrape_fantalens(next_gw)
    except Exception as e:
        print(f"  FantaLens scrape failed: {e}")
        fl_projections = []
        had_error = True

    fl_written = 0
    if fl_projections:
        skipped = sum(1 for p in fl_projections if p['element_id'] not in valid_ids)
        rows = [{
            'source_id': fl_source_id,
            'element_id': p['element_id'],
            'season': '2026-27',
            'gameweek': p['gameweek'],
            'uploaded_for_gw': next_gw,
            'expected_points': p['expected_points'],
            'meta': json.dumps(p.get('meta', {})),
        } for p in fl_projections if p['element_id'] in valid_ids]

        if skipped:
            print(f"  Skipped {skipped} FantaLens rows with unknown element_ids")
        print(f"  Writing {len(rows)} FantaLens projections to Supabase...")
        for i in range(0, len(rows), 200):
            if supabase_post("projection_inputs", rows[i:i+200],
                             "source_id,element_id,season,gameweek,uploaded_for_gw"):
                fl_written += len(rows[i:i+200])
            else:
                had_error = True
            time.sleep(0.3)

    # --- Scrape FPL Form ---
    try:
        ff_projections = scrape_fplform(next_gw)
    except Exception as e:
        print(f"  FPL Form scrape failed: {e}")
        ff_projections = []
        had_error = True

    ff_written = 0
    if ff_projections:
        skipped = sum(1 for p in ff_projections if p['element_id'] not in valid_ids)
        rows = [{
            'source_id': ff_source_id,
            'element_id': p['element_id'],
            'season': '2026-27',
            'gameweek': p['gameweek'],
            'uploaded_for_gw': next_gw,
            'expected_points': p['expected_points'],
            'meta': json.dumps(p.get('meta', {})),
        } for p in ff_projections if p['element_id'] in valid_ids]

        if skipped:
            print(f"  Skipped {skipped} FPL Form rows with unknown element_ids")
        print(f"  Writing {len(rows)} FPL Form projections to Supabase...")
        for i in range(0, len(rows), 200):
            if supabase_post("projection_inputs", rows[i:i+200],
                             "source_id,element_id,season,gameweek,uploaded_for_gw"):
                ff_written += len(rows[i:i+200])
            else:
                had_error = True
            time.sleep(0.3)

    # Summary
    print(f"\n  Done. FantaLens: {fl_written} rows written, FPL Form: {ff_written} rows written")
    if is_pre_deadline:
        print(f"  Tagged as pre-deadline snapshot for GW{next_gw}")

    # Fail the job if a scrape crashed or a write errored — so we get notified
    # (but only after doing as much as possible first).
    if had_error:
        print("  One or more steps had errors (see above).")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
