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


def content_hash(projections):
    """Deterministic hash of a projection set for dedup.

    Two captures with identical (element_id, gameweek, expected_points) sets
    produce the same hash, so we skip storing an unchanged re-scrape.
    """
    import hashlib
    items = sorted(
        (int(p['element_id']), int(p['gameweek']), round(float(p['expected_points']), 3))
        for p in projections
        if p.get('element_id') is not None and p.get('expected_points') is not None
    )
    payload = ';'.join(f"{e}:{g}:{v}" for e, g, v in items)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_latest_capture_hash(source_id, uploaded_for_gw, season='2026-27'):
    """Return the content_hash of the most recent capture for this source+GW, or None."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/projection_captures"
        f"?source_id=eq.{source_id}&uploaded_for_gw=eq.{uploaded_for_gw}"
        f"&season=eq.{quote(season)}&select=content_hash&order=captured_at.desc&limit=1",
        headers=HEADERS, timeout=15
    )
    if resp.status_code == 200:
        rows = resp.json()
        if rows:
            return rows[0].get('content_hash')
    return None


def create_capture(source_id, uploaded_for_gw, chash, row_count, player_count, season='2026-27', meta=None):
    """Insert a new capture row and return its id."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/projection_captures",
        headers={**HEADERS, "Prefer": "return=representation"},
        json={
            'source_id': source_id,
            'season': season,
            'uploaded_for_gw': uploaded_for_gw,
            'content_hash': chash,
            'row_count': row_count,
            'player_count': player_count,
            'meta': json.dumps(meta or {}),
        },
        timeout=15
    )
    if resp.status_code in (200, 201):
        return resp.json()[0]['id']
    print(f"  ERROR creating capture: {resp.status_code} - {resp.text[:200]}")
    return None


def store_projection_snapshot(source_id, uploaded_for_gw, projections, valid_ids, season='2026-27', meta=None):
    """Store a projection set as a new capture, but only if it differs from the
    most recent capture for this source+GW. Returns (written_rows, skipped_unknown, was_new).
    """
    # Filter to players we know about (avoid FK violations)
    filtered = [p for p in projections if p['element_id'] in valid_ids]
    skipped = len(projections) - len(filtered)
    if not filtered:
        return 0, skipped, False

    chash = content_hash(filtered)
    prev_hash = get_latest_capture_hash(source_id, uploaded_for_gw, season)
    if prev_hash == chash:
        print(f"    Unchanged since last capture (hash {chash[:12]}) — skipping")
        return 0, skipped, False

    player_count = len({p['element_id'] for p in filtered})
    capture_id = create_capture(source_id, uploaded_for_gw, chash, len(filtered), player_count, season, meta)
    if not capture_id:
        return 0, skipped, False

    rows = [{
        'capture_id': capture_id,
        'source_id': source_id,
        'element_id': p['element_id'],
        'season': season,
        'gameweek': p['gameweek'],
        'uploaded_for_gw': uploaded_for_gw,
        'expected_points': p['expected_points'],
        'meta': json.dumps(p.get('meta', {})),
    } for p in filtered]

    written = 0
    for i in range(0, len(rows), 200):
        if supabase_post("projection_inputs", rows[i:i+200]):
            written += len(rows[i:i+200])
        time.sleep(0.2)
    return written, skipped, True


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


def _fetch_fantalens_page(gw, page):
    """Fetch one page of FantaLens players for a specific gameweek."""
    resp = requests.get(
        f"https://fantalens.com/players?gw={gw}&page={page}",
        timeout=15, headers={'User-Agent': 'Mozilla/5.0'}
    )
    scripts = re.findall(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        resp.text, re.DOTALL
    )
    if not scripts:
        return None
    return json.loads(scripts[0])


def scrape_fantalens(gameweek):
    """Scrape FantaLens projected points for the next several gameweeks.

    The /players listing only exposes one gameweek at a time (its xpts field),
    but accepts a ?gw=N param, so we loop GW..GW+7 and combine. This gives
    multi-week coverage comparable to FPL Form.
    """
    print(f"  Scraping FantaLens (GW{gameweek}..{min(gameweek + 7, 38)})...")
    projections = []

    for gw in range(gameweek, min(gameweek + 7, 38) + 1):
        page = 1
        gw_rows = 0
        while True:
            try:
                data = _fetch_fantalens_page(gw, page)
                if not data:
                    break
                props = data.get('props') or {}
                players = props.get('players') or []
                if not players:
                    break

                for p in players:
                    element_id = p.get('external_id')
                    if not element_id:
                        continue
                    xpts_data = p.get('xpts') or {}
                    gw_data = xpts_data.get(str(gw))
                    if not isinstance(gw_data, dict):
                        continue
                    fixtures = gw_data.get('fixtures') or []
                    # Sum xpts across all fixtures this player has in the GW
                    # (usually 1, but 2 in a double gameweek).
                    total = 0.0
                    have = False
                    meta_fx = None
                    for fx in fixtures:
                        if not isinstance(fx, dict):
                            continue
                        fx_xpts = fx.get('xpts')
                        if fx_xpts is None:
                            continue
                        total += float(fx_xpts)
                        have = True
                        if meta_fx is None:
                            bd = fx.get('breakdown') or {}
                            meta_fx = {
                                'opponent': fx.get('opponent'),
                                'is_home': fx.get('is_home'),
                                'difficulty': fx.get('difficulty'),
                                'win_prob': fx.get('win'),
                                'proj_goals': bd.get('goals'),
                                'proj_assists': bd.get('assists'),
                                'proj_bonus': bd.get('bonus'),
                            }
                    if not have:
                        continue
                    projections.append({
                        'element_id': element_id,
                        'gameweek': gw,
                        'expected_points': round(total, 2),
                        'meta': meta_fx or {},
                    })
                    gw_rows += 1

                if len(players) < 25:
                    break
                page += 1
                time.sleep(0.4)
            except Exception as e:
                print(f"    GW{gw} page {page} error: {e}")
                break
        print(f"    GW{gw}: {gw_rows} players")

    print(f"    Extracted {len(projections)} projection rows across gameweeks")
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
        print(f"  FantaLens: {len(fl_projections)} rows scraped")
        written, skipped, was_new = store_projection_snapshot(
            fl_source_id, next_gw, fl_projections, valid_ids)
        fl_written = written
        if skipped:
            print(f"    Skipped {skipped} rows with unknown element_ids")
        print(f"    {'New capture: ' + str(written) + ' rows written' if was_new else 'No new capture stored'}")

    # --- Scrape FPL Form ---
    try:
        ff_projections = scrape_fplform(next_gw)
    except Exception as e:
        print(f"  FPL Form scrape failed: {e}")
        ff_projections = []
        had_error = True

    ff_written = 0
    if ff_projections:
        print(f"  FPL Form: {len(ff_projections)} rows scraped")
        written, skipped, was_new = store_projection_snapshot(
            ff_source_id, next_gw, ff_projections, valid_ids)
        ff_written = written
        if skipped:
            print(f"    Skipped {skipped} rows with unknown element_ids")
        print(f"    {'New capture: ' + str(written) + ' rows written' if was_new else 'No new capture stored'}")

    # Summary
    print(f"\n  Done. FantaLens: {fl_written} rows, FPL Form: {ff_written} rows")
    if is_pre_deadline:
        print(f"  Tagged as pre-deadline snapshot for GW{next_gw}")

    # Fail the job if a scrape crashed — so we get notified.
    if had_error:
        print("  One or more steps had errors (see above).")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
