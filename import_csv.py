#!/usr/bin/env python3
"""
Import Transfer Algorithm CSV into the database.

Handles:
- Fuzzy name matching (encoding differences, abbreviations, nicknames)
- Team abbreviation mapping (CSV uses different codes than FPL API)
- Persistent mapping: once a CSV name is matched, it stays matched
- Mid-season name changes: mapping stored in DB, editable
- Manual override: unmatched names logged for human resolution

Usage:
    python import_csv.py path/to/TransferAlgorithm.csv --season 2026-27 --gameweek 1
    python import_csv.py path/to/TransferAlgorithm.csv --season 2026-27 --gameweek 2  # re-runs matching
"""
import csv, sqlite3, os, sys, json, re
from unicodedata import normalize, category
from difflib import SequenceMatcher
from argparse import ArgumentParser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "data", "fpl_tracker.db")

# Import AI resolver (optional — works without it, just flags instead of resolving)
try:
    from ai_resolve import resolve_batch
    AI_AVAILABLE = bool(os.environ.get("GEMINI_API_KEY"))
except ImportError:
    AI_AVAILABLE = False

# CSV team abbreviations → FPL API short names
TEAM_MAP = {
    'ARS': 'ARS', 'AVL': 'AVL', 'BOU': 'BOU', 'BRE': 'BRE',
    'BRI': 'BHA', 'CHE': 'CHE', 'COV': 'COV', 'CPL': 'CRY',
    'EVE': 'EVE', 'FUL': 'FUL', 'HUL': 'HUL', 'IPS': 'IPS',
    'LEE': 'LEE', 'LIV': 'LIV', 'MCI': 'MCI', 'MUN': 'MUN',
    'NEW': 'NEW', 'NOT': 'NFO', 'SUN': 'SUN', 'TOT': 'TOT',
    # Add as needed for future seasons
    'WBA': None,  # Placeholder in CSV
    'WHU': None,  # Not in 2026-27 PL
}


def strip_accents(s):
    """Remove accents for fuzzy matching. 'Ødegaard' → 'Odegaard'."""
    nfkd = normalize('NFKD', s)
    return ''.join(c for c in nfkd if category(c) != 'Mn')


def clean_name(name):
    """Normalize a name for comparison."""
    name = name.strip()
    # Handle "(Nickname)" format: "Fernández (Enzo)" → "Fernández"
    name = re.sub(r'\s*\(.*?\)\s*', ' ', name).strip()
    # Remove encoding artifacts
    name = name.replace('�', '')
    # Normalize unicode
    name = strip_accents(name)
    # Lowercase for comparison
    return name.lower().strip()


def name_similarity(csv_name, api_name):
    """Score how similar two names are. Returns 0-1."""
    a = clean_name(csv_name)
    b = clean_name(api_name)

    # Exact match after cleaning
    if a == b:
        return 1.0

    # One contains the other (e.g. "Bruno" matches "Bruno Guimaraes")
    if a in b or b in a:
        return 0.9

    # Split into parts for structural matching
    a_parts = a.split()
    b_parts = b.split()

    # Surname match (last word of either matches last word of other)
    if a_parts and b_parts and a_parts[-1] == b_parts[-1]:
        return 0.85

    # Any word in CSV name matches any word in API name (handles "B. Guimaraes" → "Bruno Guimaraes")
    # Strip single-letter initials
    a_meaningful = [p for p in a_parts if len(p) > 1]
    b_meaningful = [p for p in b_parts if len(p) > 1]
    if a_meaningful and b_meaningful:
        shared = set(a_meaningful) & set(b_meaningful)
        if shared:
            # Shared word(s) — weight by how significant they are
            return 0.85 + 0.05 * min(len(shared), 3)

    # First word of CSV matches first word of API (both are first names)
    if a_parts and b_parts and a_parts[0] == b_parts[0] and len(a_parts[0]) > 2:
        return 0.80

    # Initial + surname: "B. Guimaraes" → check if first char matches first name
    if a_parts and b_parts and len(a_parts[0]) <= 2 and a_parts[0][0] == b_parts[0][0]:
        # First initial matches, check if remaining parts overlap
        a_rest = set(a_parts[1:])
        b_rest = set(b_parts[1:])
        if a_rest & b_rest:
            return 0.82

    # Sequence matching as fallback
    return SequenceMatcher(None, a, b).ratio()


def ensure_mapping_table(conn):
    """Create the name mapping table if it doesn't exist.
    
    Design: The mapping table stores ALL csv_name → element_id associations
    ever seen. Multiple CSV names can map to the same element_id (because 
    the creator might change how they write a name mid-season). Each import 
    re-runs matching from scratch but uses the mapping table as a boost —
    if we've seen this exact string before, trust the stored answer.
    If it's new, fuzzy match and add it.
    """
    conn.execute("""CREATE TABLE IF NOT EXISTS csv_name_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        csv_name TEXT NOT NULL,
        csv_team TEXT NOT NULL,
        element_id INTEGER,
        confidence REAL,
        source TEXT DEFAULT 'auto',  -- 'auto', 'manual', 'confirmed'
        season TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        notes TEXT,
        UNIQUE(csv_name, csv_team, season)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS csv_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        season TEXT NOT NULL,
        gameweek INTEGER NOT NULL,
        element_id INTEGER,
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
        imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(season, gameweek, element_id)
    )""")


def get_existing_mappings(conn, season):
    """Load previously confirmed mappings."""
    rows = conn.execute(
        "SELECT csv_name, csv_team, element_id, confidence, source FROM csv_name_mapping WHERE season = ?",
        (season,)).fetchall()
    # Key by (csv_name, csv_team)
    return {(r[0], r[1]): {'element_id': r[2], 'confidence': r[3], 'source': r[4]} for r in rows}


def get_api_players(conn):
    """Get all players from the DB with team info."""
    rows = conn.execute("""
        SELECT p.element_id, p.web_name, p.first_name, p.second_name, p.team_id, t.short_name
        FROM players p
        JOIN teams t ON p.team_id = t.team_id
    """).fetchall()
    return [{'id': r[0], 'web_name': r[1], 'first_name': r[2],
             'second_name': r[3], 'team_id': r[4], 'team_short': r[5]} for r in rows]


def match_player(csv_name, csv_team_api, api_players):
    """Find the best matching API player for a CSV name+team."""
    # Filter to same team first
    team_players = [p for p in api_players if p['team_short'] == csv_team_api]

    if not team_players:
        return None, 0.0

    best_match = None
    best_score = 0.0

    for p in team_players:
        # Try matching against web_name, full name, last name
        candidates = [
            p['web_name'],
            f"{p['first_name']} {p['second_name']}",
            p['second_name'],
            p['first_name'],
        ]

        for candidate in candidates:
            score = name_similarity(csv_name, candidate)
            if score > best_score:
                best_score = score
                best_match = p

    return best_match, best_score


def parse_csv(filepath):
    """Parse the Transfer Algorithm CSV."""
    rows = []
    with open(filepath, 'r', encoding='latin-1') as f:
        reader = csv.reader(f)
        header = next(reader)

        for row in reader:
            if len(row) < 10:
                continue
            name = row[3].strip()
            team = row[4].strip()
            if not name or name == '0' or not team:
                continue

            try:
                bcv = float(row[1].strip()) if row[1].strip() and row[1].strip() != '-' else None
                position = row[2].strip()
                price = float(row[5].strip()) if row[5].strip() and row[5].strip() != '-' else None
                w_mins = float(row[6].strip()) if row[6].strip() and row[6].strip() != '-' else None
                w_uppm = float(row[7].strip()) if row[7].strip() and row[7].strip() != '-' else None
                ppg_lt = float(row[8].strip()) if row[8].strip() and row[8].strip() != '-' else None
                fix_ratio_str = row[9].strip().replace('%', '')
                fix_ratio = float(fix_ratio_str) / 100 if fix_ratio_str and fix_ratio_str != '-' else None

                # GW projections (columns 10-17)
                gw_proj = []
                for i in range(10, min(18, len(row))):
                    val = row[i].strip()
                    gw_proj.append(float(val) if val and val != '-' else None)
                # Pad to 8
                while len(gw_proj) < 8:
                    gw_proj.append(None)

                # Sum (column 18 if exists)
                proj_sum = None
                if len(row) > 18:
                    val = row[18].strip()
                    proj_sum = float(val) if val and val != '-' else None

                rows.append({
                    'name': name, 'team': team, 'position': position,
                    'bcv': bcv, 'price': price,
                    'weighted_minutes': w_mins, 'weighted_uppm': w_uppm,
                    'ppg_longer_term': ppg_lt, 'fixture_ratio': fix_ratio,
                    'projected_sum': proj_sum,
                    'gw_proj': gw_proj,
                })
            except (ValueError, IndexError):
                continue

    return rows


def import_csv(filepath, season, gameweek):
    """Main import function."""
    conn = sqlite3.connect(DB_PATH)
    ensure_mapping_table(conn)

    # Parse CSV
    csv_rows = parse_csv(filepath)
    print(f"Parsed {len(csv_rows)} players from CSV")

    # Get existing mappings and API players
    mappings = get_existing_mappings(conn, season)
    api_players = get_api_players(conn)
    print(f"Existing mappings: {len(mappings)}, API players: {len(api_players)}")

    matched = 0
    unmatched = []
    low_confidence = []
    needs_ai = []  # Players that fuzzy matching couldn't resolve confidently

    for row in csv_rows:
        csv_name = row['name']
        csv_team = row['team']
        csv_team_api = TEAM_MAP.get(csv_team)

        if csv_team_api is None:
            continue  # Skip placeholder teams

        # Check existing mapping first
        key = (csv_name, csv_team)
        if key in mappings:
            element_id = mappings[key]['element_id']
            if element_id:
                matched += 1
                _insert_import(conn, season, gameweek, element_id, row)
                continue

        # Try to match
        best_match, score = match_player(csv_name, csv_team_api, api_players)

        if best_match and score >= 0.85:
            # High confidence — accept automatically
            element_id = best_match['id']
            conn.execute("""INSERT OR REPLACE INTO csv_name_mapping
                (csv_name, csv_team, element_id, confidence, source, season)
                VALUES (?, ?, ?, ?, 'auto', ?)""",
                (csv_name, csv_team, element_id, score, season))
            _insert_import(conn, season, gameweek, element_id, row)
            matched += 1
        elif best_match and score >= 0.75:
            # Medium confidence — accept but flag for review
            element_id = best_match['id']
            conn.execute("""INSERT OR REPLACE INTO csv_name_mapping
                (csv_name, csv_team, element_id, confidence, source, season)
                VALUES (?, ?, ?, ?, 'auto', ?)""",
                (csv_name, csv_team, element_id, score, season))
            _insert_import(conn, season, gameweek, element_id, row)
            matched += 1
            low_confidence.append((csv_name, csv_team, best_match['web_name'], score))
        else:
            # Low confidence — queue for AI resolution
            needs_ai.append({
                'csv_name': csv_name, 'csv_team': csv_team_api,
                'csv_position': row['position'], 'csv_price': row['price'],
                'row': row, 'best_guess': best_match, 'score': score,
            })

    # AI RESOLUTION STEP — only for players the fuzzy matcher couldn't handle
    if needs_ai and AI_AVAILABLE:
        print(f"\n  Attempting AI resolution for {len(needs_ai)} uncertain matches...")
        ai_resolved = resolve_batch(needs_ai, conn)

        for player_info in needs_ai:
            key = (player_info['csv_name'], player_info['csv_team'])
            if key in ai_resolved:
                element_id = ai_resolved[key]
                conn.execute("""INSERT OR REPLACE INTO csv_name_mapping
                    (csv_name, csv_team, element_id, confidence, source, season, notes)
                    VALUES (?, ?, ?, 0.95, 'ai', ?, 'Resolved by Gemini')""",
                    (player_info['csv_name'], player_info['csv_team'],  # Use original csv_team not API team
                     element_id, season))
                # Need to map back to original csv_team for the mapping table
                # Fix: find original csv_team from the row
                orig_team = player_info['row']['team']
                conn.execute("""INSERT OR REPLACE INTO csv_name_mapping
                    (csv_name, csv_team, element_id, confidence, source, season, notes)
                    VALUES (?, ?, ?, 0.95, 'ai', ?, 'Resolved by Gemini')""",
                    (player_info['csv_name'], orig_team, element_id, season))
                _insert_import(conn, season, gameweek, element_id, player_info['row'])
                matched += 1
            else:
                # AI couldn't resolve either — truly unmatched
                best = player_info['best_guess']
                conn.execute("""INSERT OR REPLACE INTO csv_name_mapping
                    (csv_name, csv_team, element_id, confidence, source, season, notes)
                    VALUES (?, ?, NULL, ?, 'unmatched', ?, ?)""",
                    (player_info['csv_name'], player_info['row']['team'],
                     player_info['score'] if best else 0, season,
                     f"Best guess: {best['web_name'] if best else 'none'} ({player_info['score']:.2f})"))
                unmatched.append((player_info['csv_name'], player_info['row']['team'],
                                  best['web_name'] if best else '???', player_info['score']))
    elif needs_ai:
        # No AI available — all go to unmatched
        if needs_ai:
            print(f"\n  {len(needs_ai)} players need resolution (no GEMINI_API_KEY set)")
        for player_info in needs_ai:
            best = player_info['best_guess']
            conn.execute("""INSERT OR REPLACE INTO csv_name_mapping
                (csv_name, csv_team, element_id, confidence, source, season, notes)
                VALUES (?, ?, NULL, ?, 'unmatched', ?, ?)""",
                (player_info['csv_name'], player_info['row']['team'],
                 player_info['score'] if best else 0, season,
                 f"Best guess: {best['web_name'] if best else 'none'} ({player_info['score']:.2f})"))
            unmatched.append((player_info['csv_name'], player_info['row']['team'],
                              best['web_name'] if best else '???', player_info['score']))

    conn.commit()

    # Report
    print(f"\n{'='*60}")
    print(f"IMPORT RESULTS: Season {season}, GW{gameweek}")
    print(f"{'='*60}")
    print(f"  Matched:        {matched}/{len(csv_rows)} ({matched/len(csv_rows)*100:.1f}%)")
    print(f"  Unmatched:      {len(unmatched)}")
    print(f"  Low confidence: {len(low_confidence)}")

    if low_confidence:
        print(f"\n  LOW CONFIDENCE MATCHES (check these):")
        for csv_n, csv_t, api_n, sc in sorted(low_confidence, key=lambda x: x[3]):
            print(f"    {csv_n:<25} ({csv_t}) → {api_n:<20} (score: {sc:.2f})")

    if unmatched:
        print(f"\n  UNMATCHED (need manual mapping):")
        for csv_n, csv_t, best, sc in sorted(unmatched, key=lambda x: x[1]):
            print(f"    {csv_n:<25} ({csv_t}) ~ {best:<20} (score: {sc:.2f})")
        print(f"\n  To fix: UPDATE csv_name_mapping SET element_id=X, source='manual'")
        print(f"          WHERE csv_name='...' AND csv_team='...' AND season='{season}';")

    conn.close()
    return matched, len(unmatched)


def _insert_import(conn, season, gameweek, element_id, row):
    """Insert a single CSV row into csv_imports."""
    gw = row['gw_proj']
    conn.execute("""INSERT OR REPLACE INTO csv_imports
        (season, gameweek, element_id, csv_name, csv_team, position,
         bcv, projected_sum, csv_price, weighted_minutes, weighted_uppm,
         ppg_longer_term, fixture_ratio,
         gw1, gw2, gw3, gw4, gw5, gw6, gw7, gw8)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (season, gameweek, element_id, row['name'], row['team'], row['position'],
         row['bcv'], row['projected_sum'], row['price'],
         row['weighted_minutes'], row['weighted_uppm'],
         row['ppg_longer_term'], row['fixture_ratio'],
         gw[0], gw[1], gw[2], gw[3], gw[4], gw[5], gw[6], gw[7]))


if __name__ == "__main__":
    parser = ArgumentParser(description="Import Transfer Algorithm CSV")
    parser.add_argument("csv_path", help="Path to TransferAlgorithm CSV file")
    parser.add_argument("--season", required=True, help="Season (e.g. 2026-27)")
    parser.add_argument("--gameweek", type=int, required=True, help="Gameweek number")
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        print(f"Error: {args.csv_path} not found")
        sys.exit(1)

    import_csv(args.csv_path, args.season, args.gameweek)
