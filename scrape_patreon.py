#!/usr/bin/env python3
"""
Fetch the latest Transfer Algorithm CSV from Patreon and import it.

Checks for new posts, downloads CSV attachment if a new GW is available,
runs through name matching, and stores in Supabase.

Requires: PATREON_SESSION, SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import requests, json, os, re, csv, time
from io import StringIO
from datetime import datetime, timezone
from urllib.parse import quote
from unicodedata import normalize, category

PATREON_SESSION = os.environ.get("PATREON_SESSION", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
CAMPAIGN_ID = "1982496"  # TransferAlgorithm

HEADERS_PATREON = {
    'User-Agent': 'Mozilla/5.0',
    'Cookie': f'session_id={PATREON_SESSION}',
}

HEADERS_SUPABASE = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

TEAM_MAP = {
    'ARS': 'ARS', 'AVL': 'AVL', 'BOU': 'BOU', 'BRE': 'BRE',
    'BRI': 'BHA', 'CHE': 'CHE', 'COV': 'COV', 'CPL': 'CRY',
    'EVE': 'EVE', 'FUL': 'FUL', 'HUL': 'HUL', 'IPS': 'IPS',
    'LEE': 'LEE', 'LIV': 'LIV', 'MCI': 'MCI', 'MUN': 'MUN',
    'NEW': 'NEW', 'NOT': 'NFO', 'SUN': 'SUN', 'TOT': 'TOT',
}


def strip_accents(s):
    return ''.join(c for c in normalize('NFKD', s) if category(c) != 'Mn').lower()


def supabase_get(path):
    resp = requests.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HEADERS_SUPABASE, timeout=15)
    return resp.json() if resp.status_code == 200 else []


def supabase_delete(table, filters):
    """Delete rows matching filters (dict of column -> value, exact match)."""
    query = "&".join(f"{k}=eq.{quote(str(v))}" for k, v in filters.items())
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    resp = requests.delete(url, headers=HEADERS_SUPABASE, timeout=30)
    if resp.status_code not in (200, 204):
        print(f"  DELETE {table} failed: {resp.status_code} - {resp.text[:150]}")
        return False
    return True


def supabase_post(table, data, upsert_cols=None):
    headers = dict(HEADERS_SUPABASE)
    if upsert_cols:
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={upsert_cols}"
    else:
        headers["Prefer"] = "return=minimal"
        url = f"{SUPABASE_URL}/rest/v1/{table}"
    for i in range(0, len(data), 200):
        resp = requests.post(url, headers=headers, json=data[i:i+200], timeout=30)
        if resp.status_code not in (200, 201, 204):
            print(f"  ERROR {table}: {resp.status_code} - {resp.text[:200]}")
            return False
    return True


def projection_content_hash(proj_rows):
    """Deterministic hash of a projection set for dedup.

    Includes bcv so that a change in the value figure (even with identical
    expected points) produces a new capture.
    """
    import hashlib

    def bcv_of(r):
        m = r.get('meta')
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except (json.JSONDecodeError, TypeError):
                m = {}
        if isinstance(m, dict) and m.get('bcv') is not None:
            return round(float(m['bcv']), 4)
        return None

    items = sorted(
        (int(r['element_id']), int(r['gameweek']),
         round(float(r['expected_points']), 3), bcv_of(r))
        for r in proj_rows
        if r.get('element_id') is not None and r.get('expected_points') is not None
    )
    payload = ';'.join(f"{e}:{g}:{v}:{b}" for e, g, v, b in items)
    return hashlib.sha256(payload.encode()).hexdigest()


def store_projection_capture(source_id, uploaded_for_gw, proj_rows, season='2026-27', meta=None):
    """Store projections as a new capture — but only if the content differs from
    the most recent capture for this source+GW. Returns (written, was_new)."""
    if not proj_rows:
        return 0, False

    chash = projection_content_hash(proj_rows)

    # Compare with latest capture's hash
    prev = supabase_get(
        f"projection_captures?source_id=eq.{source_id}&uploaded_for_gw=eq.{uploaded_for_gw}"
        f"&season=eq.{season}&select=content_hash&order=captured_at.desc&limit=1"
    )
    if isinstance(prev, list) and prev and prev[0].get('content_hash') == chash:
        print(f"  Projections unchanged since last capture — skipping")
        return 0, False

    # Create the capture
    player_count = len({r['element_id'] for r in proj_rows})
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/projection_captures",
        headers={**HEADERS_SUPABASE, "Prefer": "return=representation"},
        json={
            'source_id': source_id, 'season': season, 'uploaded_for_gw': uploaded_for_gw,
            'content_hash': chash, 'row_count': len(proj_rows), 'player_count': player_count,
            'meta': json.dumps(meta or {}),
        },
        timeout=15
    )
    if resp.status_code not in (200, 201):
        print(f"  ERROR creating capture: {resp.status_code} - {resp.text[:200]}")
        return 0, False
    capture_id = resp.json()[0]['id']

    rows = [{**r, 'capture_id': capture_id} for r in proj_rows]
    ok = supabase_post("projection_inputs", rows)
    if ok:
        return len(rows), True
    return 0, False


def get_latest_post():
    """Check Patreon for the latest Transfer Algorithm post."""
    url = f"https://www.patreon.com/api/posts?filter[campaign_id]={CAMPAIGN_ID}&filter[is_draft]=false&sort=-published_at&page[count]=5"
    resp = requests.get(url, headers=HEADERS_PATREON, timeout=15)
    if resp.status_code != 200:
        print(f"  Patreon API error: {resp.status_code}")
        return None

    data = resp.json()
    posts = data.get('data', [])

    # Find the latest post with "Transfer Algorithm" in the title (not "my team")
    for post in posts:
        title = post.get('attributes', {}).get('title', '')
        if 'transfer algorithm' in title.lower() and 'my team' not in title.lower():
            # Extract GW number from title
            gw_match = re.search(r'GW\s*(\d+)', title, re.IGNORECASE)
            if gw_match:
                return {
                    'post_id': post['id'],
                    'title': title,
                    'gameweek': int(gw_match.group(1)),
                    'published_at': post['attributes']['published_at'],
                }
    return None


def get_post_csv(post_id):
    """Download the CSV from a Patreon post.

    The CSV is delivered as a 'media' item (under attachments_media), with
    file_name + download_url fields. Older Patreon posts used 'attachment'
    items with name + url — we handle both.
    """
    # Request all attachment/media relationships and their file fields
    includes = "attachments,attachments_media,media"
    fields = "fields[post]=title,content&fields[media]=file_name,download_url,mimetype&fields[attachment]=name,url"
    url = f"https://www.patreon.com/api/posts/{post_id}?include={includes}&{fields}"
    resp = requests.get(url, headers=HEADERS_PATREON, timeout=15)
    if resp.status_code != 200:
        print(f"  Post fetch error: {resp.status_code}")
        return None

    data = resp.json()
    included = data.get('included', [])

    # 1. Look for a media item that is a CSV (current Patreon format)
    for item in included:
        if item.get('type') == 'media':
            attrs = item.get('attributes', {})
            fname = (attrs.get('file_name') or '').lower()
            mimetype = (attrs.get('mimetype') or '').lower()
            dl = attrs.get('download_url', '')
            if fname.endswith('.csv') or 'csv' in mimetype or 'transferalgorithm' in fname:
                print(f"  Found CSV media: {attrs.get('file_name')}")
                csv_resp = requests.get(dl, headers=HEADERS_PATREON, timeout=30)
                if csv_resp.status_code == 200:
                    return csv_resp.content
                print(f"  Download failed: {csv_resp.status_code}")

    # 2. Legacy: look for an 'attachment' item
    for item in included:
        if item.get('type') == 'attachment':
            attrs = item.get('attributes', {})
            name = (attrs.get('name') or '').lower()
            att_url = attrs.get('url', '')
            if name.endswith('.csv') or 'transferalgorithm' in name:
                print(f"  Found attachment: {attrs.get('name')}")
                csv_resp = requests.get(att_url, headers=HEADERS_PATREON, timeout=30)
                if csv_resp.status_code == 200:
                    return csv_resp.content
                print(f"  Download failed: {csv_resp.status_code}")

    # 3. Fallback: CSV link embedded in post content
    content = data.get('data', {}).get('attributes', {}).get('content', '')
    if content:
        csv_links = re.findall(r'https?://[^\s"<]+\.csv[^\s"<]*', content)
        for link in csv_links:
            print(f"  Found CSV link in content: {link}")
            csv_resp = requests.get(link, headers=HEADERS_PATREON, timeout=30)
            if csv_resp.status_code == 200:
                return csv_resp.content

    print("  No CSV attachment found")
    return None


def get_last_import_timestamp():
    """Get the published_at of the last Patreon post we imported.

    Stored appended to the transfer_algorithm row's description field as
    a marker: "...|last_import=<iso timestamp>".
    """
    rows = supabase_get("projection_sources?source_name=eq.transfer_algorithm&select=description")
    if isinstance(rows, list) and rows and rows[0].get('description'):
        desc = rows[0]['description']
        if '|last_import=' in desc:
            return desc.split('|last_import=', 1)[1].strip()
    return None


def set_last_import_timestamp(published_at):
    """Store the published_at in the transfer_algorithm description field."""
    rows = supabase_get("projection_sources?source_name=eq.transfer_algorithm&select=id,description")
    if not (isinstance(rows, list) and rows):
        return False
    source_id = rows[0]['id']
    base_desc = (rows[0].get('description') or '').split('|last_import=', 1)[0].rstrip()
    new_desc = f"{base_desc}|last_import={published_at}"
    headers = dict(HEADERS_SUPABASE)
    headers["Prefer"] = "return=minimal"
    url = f"{SUPABASE_URL}/rest/v1/projection_sources?id=eq.{source_id}"
    resp = requests.patch(url, headers=headers, json={'description': new_desc}, timeout=15)
    if resp.status_code not in (200, 204):
        print(f"  WARNING: could not save timestamp: {resp.status_code} - {resp.text[:150]}")
        return False
    return True


def get_players_db():
    """Get all players for name matching."""
    rows = supabase_get("players?select=element_id,web_name,first_name,second_name,team_id,teams!inner(short_name)")
    result = []
    for r in rows:
        team = r.get('teams')
        if isinstance(team, list):
            team = team[0] if team else {}
        result.append({
            'element_id': r['element_id'],
            'web_name': r['web_name'],
            'first_name': r.get('first_name', ''),
            'second_name': r.get('second_name', ''),
            'team_short': team.get('short_name', ''),
        })
    return result


def get_existing_mappings(season):
    """Get existing name mappings."""
    rows = supabase_get(f"csv_name_mapping?select=csv_name,csv_team,element_id&season=eq.{season}&element_id=not.is.null")
    return {(r['csv_name'], r['csv_team']): r['element_id'] for r in rows}


def match_player(csv_name, csv_team_api, players):
    """Fuzzy match a CSV name against the player database."""
    a = strip_accents(csv_name).replace('(', '').replace(')', '').strip()
    best_match = None
    best_score = 0

    for p in players:
        if p['team_short'] != csv_team_api:
            continue
        for candidate in [p['web_name'], f"{p['first_name']} {p['second_name']}", p['second_name']]:
            b = strip_accents(candidate)
            if a == b:
                return p['element_id'], 1.0
            if a in b or b in a:
                score = 0.9
            elif a.split() and b.split() and a.split()[-1] == b.split()[-1]:
                score = 0.85
            else:
                from difflib import SequenceMatcher
                score = SequenceMatcher(None, a, b).ratio()
            if score > best_score:
                best_score = score
                best_match = p['element_id']

    return best_match, best_score


def parse_bcv(raw):
    """Parse a BCV cell. The CSV uses accounting notation where parentheses
    denote negatives, e.g. '(0.15)' = -0.15. Also strips % and whitespace.
    Returns a float, or None if blank/unparseable."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == '-':
        return None
    negative = s.startswith('(') and s.endswith(')')
    s = s.strip('()').replace('%', '').replace(',', '').strip()
    if not s or s == '-':
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def import_csv(csv_bytes, gameweek, season='2026-27', published_at=None):
    """Parse CSV and import into Supabase."""
    text = csv_bytes.decode('latin-1')
    reader = csv.reader(StringIO(text))
    header = next(reader)

    players_db = get_players_db()
    mappings = get_existing_mappings(season)

    matched = 0
    unmatched = []
    imports = []
    new_mappings = []

    for row in reader:
        if len(row) < 10:
            continue
        name = row[3].strip()
        team = row[4].strip()
        if not name or name == '0' or not team:
            continue

        team_api = TEAM_MAP.get(team)
        if not team_api:
            continue

        try:
            bcv = parse_bcv(row[1])
            position = row[2].strip()
            price = float(row[5].strip()) if row[5].strip() and row[5].strip() != '-' else None
            gw_proj = []
            for i in range(10, min(18, len(row))):
                val = row[i].strip()
                gw_proj.append(float(val) if val and val != '-' else None)
            while len(gw_proj) < 8:
                gw_proj.append(None)
            proj_sum = float(row[18].strip()) if len(row) > 18 and row[18].strip() and row[18].strip() != '-' else None
        except (ValueError, IndexError):
            continue

        # Try existing mapping
        key = (name, team)
        element_id = mappings.get(key)

        if not element_id:
            # Fuzzy match
            eid, score = match_player(name, team_api, players_db)
            if eid and score >= 0.75:
                element_id = eid
                new_mappings.append({
                    'csv_name': name, 'csv_team': team, 'element_id': element_id,
                    'confidence': score, 'source': 'auto', 'season': season,
                })
            else:
                new_mappings.append({
                    'csv_name': name, 'csv_team': team, 'element_id': None,
                    'confidence': score or 0, 'source': 'unmatched', 'season': season,
                    'notes': f"Best guess: {eid} ({score:.2f})" if eid else "No match found",
                })
                unmatched.append(name)
                continue

        matched += 1
        imports.append({
            'season': season, 'gameweek': gameweek, 'element_id': element_id,
            'csv_name': name, 'csv_team': team, 'position': position,
            'bcv': bcv, 'projected_sum': proj_sum, 'csv_price': price,
            'gw1': gw_proj[0], 'gw2': gw_proj[1], 'gw3': gw_proj[2], 'gw4': gw_proj[3],
            'gw5': gw_proj[4], 'gw6': gw_proj[5], 'gw7': gw_proj[6], 'gw8': gw_proj[7],
        })

    # Write to Supabase
    write_ok = False
    if new_mappings:
        supabase_post("csv_name_mapping", new_mappings, "csv_name,csv_team,season")

    if imports:
        # csv_imports: upsert on the same constraint the admin importer uses successfully
        ok = supabase_post("csv_imports", imports, "season,gameweek,element_id")
        if not ok:
            print("  WARNING: csv_imports write failed (continuing to projection_inputs)")

        # projection_inputs — this is what the app reads. Runs regardless of above.
        # Stored as a deduplicated capture: only creates a new capture if the
        # projection values differ from the most recent one for this GW.
        sources = supabase_get("projection_sources?source_name=eq.transfer_algorithm&select=id")
        if sources:
            source_id = sources[0]['id']
            proj_rows = []
            for imp in imports:
                gws = [imp['gw1'], imp['gw2'], imp['gw3'], imp['gw4'],
                       imp['gw5'], imp['gw6'], imp['gw7'], imp['gw8']]
                for i, pts in enumerate(gws):
                    if pts is None:
                        continue
                    actual_gw = gameweek + i
                    if actual_gw > 38:
                        continue
                    proj_rows.append({
                        'source_id': source_id,
                        'element_id': imp['element_id'],
                        'season': season,
                        'gameweek': actual_gw,
                        'uploaded_for_gw': gameweek,
                        'expected_points': pts,
                        'meta': json.dumps({'bcv': imp.get('bcv')}),
                    })
            if proj_rows:
                written, was_new = store_projection_capture(
                    source_id, gameweek, proj_rows, season,
                    meta={'patreon_published_at': published_at} if published_at else None)
                if was_new:
                    print(f"  projection_inputs: new capture, {written} rows")
                    write_ok = True
                else:
                    print("  projection_inputs: no new capture (unchanged)")
                    write_ok = True  # unchanged is still a successful outcome

    return matched, unmatched, write_ok


def should_check_now():
    """Always check. The check is one cheap API call — there's no reason to skip.

    Previously this gated on time-to-deadline and time-of-day, but that caused
    us to MISS posts the creator published outside those windows. The whole point
    of checking frequently is to never miss an update, so we always run.
    """
    return True


def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Patreon Transfer Algorithm scraper")

    if not PATREON_SESSION:
        print("  ERROR: No PATREON_SESSION set")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  ERROR: No Supabase credentials")
        return

    # Frequency gating
    if not should_check_now():
        return

    # Check for new post
    latest = get_latest_post()
    if not latest:
        print("  No Transfer Algorithm post found")
        return

    print(f"  Latest post: '{latest['title']}' (GW{latest['gameweek']}, published {latest['published_at']})")

    # Check if this post is newer than what we last imported
    last_published = get_last_import_timestamp()
    force = os.environ.get("FORCE_REIMPORT", "").strip().lower() in ("1", "true", "yes")
    if last_published and latest['published_at'] <= last_published and not force:
        print(f"  Already imported this version (post published {latest['published_at']}, last import from {last_published}). Nothing to do.")
        return
    if force:
        print("  FORCE_REIMPORT set — re-importing regardless of timestamp")

    # New or updated post - download CSV
    print(f"  New/updated post detected! Downloading CSV...")
    csv_bytes = get_post_csv(latest['post_id'])
    if not csv_bytes:
        print("  ERROR: Could not download CSV")
        return

    print(f"  CSV downloaded: {len(csv_bytes)} bytes")

    # Import
    matched, unmatched, write_ok = import_csv(csv_bytes, latest['gameweek'], published_at=latest['published_at'])
    print(f"  Import complete: {matched} matched, {len(unmatched)} unmatched")
    # Only mark this version as imported if the data actually landed in the DB.
    # Otherwise we'll retry on the next run instead of silently skipping.
    if matched > 0 and write_ok:
        set_last_import_timestamp(latest['published_at'])
        print(f"  Saved import timestamp: {latest['published_at']}")
    elif matched > 0 and not write_ok:
        print("  Data write failed — NOT saving timestamp, will retry next run")
    if unmatched:
        print(f"  Unmatched: {unmatched[:10]}")
        if len(unmatched) > 10:
            print(f"    ... and {len(unmatched) - 10} more")


if __name__ == "__main__":
    main()
