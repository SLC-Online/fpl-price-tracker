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
    """Download the CSV attachment from a Patreon post."""
    # Fetch full post with includes
    url = f"https://www.patreon.com/api/posts/{post_id}?include=attachments&fields[post]=title,content&fields[attachment]=name,url"
    resp = requests.get(url, headers=HEADERS_PATREON, timeout=15)
    if resp.status_code != 200:
        print(f"  Post fetch error: {resp.status_code}")
        return None

    data = resp.json()
    included = data.get('included', [])

    # Find CSV attachment
    for item in included:
        if item.get('type') == 'attachment':
            attrs = item.get('attributes', {})
            name = attrs.get('name', '')
            url = attrs.get('url', '')
            if name.lower().endswith('.csv') or 'transferalgorithm' in name.lower():
                print(f"  Found attachment: {name}")
                # Download the CSV
                csv_resp = requests.get(url, headers=HEADERS_PATREON, timeout=30)
                if csv_resp.status_code == 200:
                    return csv_resp.content
                else:
                    print(f"  Download failed: {csv_resp.status_code}")

    # Maybe the CSV link is in the post content
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
    
    Stores tracking info in a special row in csv_imports with element_id=0.
    """
    rows = supabase_get("csv_imports?select=csv_name&season=eq.2026-27&element_id=eq.0&csv_team=eq.__patreon_meta__&limit=1")
    if rows and rows[0].get('csv_name'):
        return rows[0]['csv_name']  # We store published_at in csv_name for the meta row
    return None


def set_last_import_timestamp(published_at):
    """Store the published_at of the post we just imported."""
    meta_row = [{
        'season': '2026-27',
        'gameweek': 0,
        'element_id': 0,
        'csv_name': published_at,
        'csv_team': '__patreon_meta__',
        'position': 'META',
    }]
    supabase_post("csv_imports", meta_row, "season,element_id,csv_team")


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
            bcv = float(row[1].strip()) if row[1].strip() and row[1].strip() != '-' else None
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
    if new_mappings:
        supabase_post("csv_name_mapping", new_mappings, "csv_name,csv_team,season")

    if imports:
        supabase_post("csv_imports", imports, "season,gameweek,element_id")

        # Also populate projection_inputs
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
                supabase_post("projection_inputs", proj_rows, "source_id,element_id,season,gameweek,uploaded_for_gw")

    return matched, unmatched


def main():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] Patreon Transfer Algorithm scraper")

    if not PATREON_SESSION:
        print("  ERROR: No PATREON_SESSION set")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("  ERROR: No Supabase credentials")
        return

    # Check for new post
    latest = get_latest_post()
    if not latest:
        print("  No Transfer Algorithm post found")
        return

    print(f"  Latest post: '{latest['title']}' (GW{latest['gameweek']}, published {latest['published_at']})")

    # Check if this post is newer than what we last imported
    last_published = get_last_import_timestamp()
    if last_published and latest['published_at'] <= last_published:
        print(f"  Already imported this version (post published {latest['published_at']}, last import from {last_published}). Nothing to do.")
        return

    # New or updated post - download CSV
    print(f"  New/updated post detected! Downloading CSV...")
    csv_bytes = get_post_csv(latest['post_id'])
    if not csv_bytes:
        print("  ERROR: Could not download CSV")
        return

    print(f"  CSV downloaded: {len(csv_bytes)} bytes")

    # Import (upserts — will overwrite previous values for same GW)
    matched, unmatched = import_csv(csv_bytes, latest['gameweek'], published_at=latest['published_at'])
    print(f"  Import complete: {matched} matched, {len(unmatched)} unmatched")
    if matched > 0:
        set_last_import_timestamp(latest['published_at'])
        print(f"  Saved import timestamp: {latest['published_at']}")
    if unmatched:
        print(f"  Unmatched: {unmatched[:10]}")
        if len(unmatched) > 10:
            print(f"    ... and {len(unmatched) - 10} more")


if __name__ == "__main__":
    main()
