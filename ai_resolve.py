#!/usr/bin/env python3
"""
AI-assisted player name resolution using Google Gemini.
Only called for uncertain matches (confidence 0.5-0.85).
Uses price + position + team as additional context.

Requires: GEMINI_API_KEY environment variable
"""
import os, json, requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


def resolve_player(csv_name, csv_team, csv_position, csv_price, candidates):
    """
    Ask Gemini to identify which API player matches the CSV entry.
    
    Args:
        csv_name: Name as written in the Transfer Algorithm CSV
        csv_team: Team abbreviation from CSV (already mapped to API format)
        csv_position: Position from CSV (GK, D, M, F)
        csv_price: Price from CSV (e.g. 6.5)
        candidates: List of dicts with {id, web_name, first_name, second_name, price}
                    — all players on that team in that position (±0.5 price range)
    
    Returns:
        element_id of best match, or None if AI is uncertain
    """
    if not GEMINI_API_KEY:
        return None

    if not candidates:
        return None

    # Build candidate list for the prompt
    candidate_str = "\n".join([
        f"  ID {c['id']}: {c['web_name']} (full name: {c['first_name']} {c['second_name']}, price: £{c['price']/10:.1f})"
        for c in candidates
    ])

    prompt = f"""You are helping match player names between two football (soccer) data sources.

The Transfer Algorithm CSV has this player:
  Name: "{csv_name}"
  Team: {csv_team}
  Position: {csv_position}
  Price: £{csv_price}

The FPL API has these players on the same team in a similar position/price range:
{candidate_str}

Which API player (by ID) is the same person as the CSV entry "{csv_name}"?
Consider:
- The CSV name might be a nickname, shortened version, or have encoding issues
- Price should match within £0.5 (there may have been a recent price change)
- Position should match (GK=goalkeeper, D=defender, M=midfielder, F=forward)

Reply with ONLY the numeric ID of the matching player, or "NONE" if you cannot determine a match with confidence.
Do not explain your reasoning."""

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "maxOutputTokens": 20,
                }
            },
            timeout=10
        )

        if resp.status_code != 200:
            print(f"  [AI] Gemini API error: {resp.status_code}")
            return None

        data = resp.json()
        answer = data['candidates'][0]['content']['parts'][0]['text'].strip()

        if answer == "NONE":
            return None

        # Extract numeric ID
        try:
            element_id = int(answer)
            # Verify it's actually in our candidates
            if any(c['id'] == element_id for c in candidates):
                return element_id
            else:
                print(f"  [AI] Returned ID {element_id} not in candidates")
                return None
        except ValueError:
            # Try to extract a number from the response
            import re
            nums = re.findall(r'\d+', answer)
            if nums:
                element_id = int(nums[0])
                if any(c['id'] == element_id for c in candidates):
                    return element_id
            return None

    except Exception as e:
        print(f"  [AI] Error: {e}")
        return None


def resolve_batch(unmatched_players, conn):
    """
    Attempt to resolve a batch of unmatched players using AI.
    
    Args:
        unmatched_players: List of dicts with {csv_name, csv_team, csv_position, csv_price}
        conn: SQLite connection to fpl_tracker.db
    
    Returns:
        Dict of {(csv_name, csv_team): element_id} for resolved players
    """
    if not GEMINI_API_KEY:
        print("  [AI] No GEMINI_API_KEY set — skipping AI resolution")
        return {}

    resolved = {}
    pos_map = {'GK': 1, 'D': 2, 'M': 3, 'F': 4}

    for player in unmatched_players:
        csv_name = player['csv_name']
        csv_team = player['csv_team']
        csv_pos = player['csv_position']
        csv_price = player['csv_price']

        if csv_price is None:
            continue

        # Get candidates: same team, expand to ±1.0 price range, any position
        # (position might differ between CSV and API classification)
        price_min = int((csv_price - 1.0) * 10)
        price_max = int((csv_price + 1.0) * 10)

        candidates = conn.execute("""
            SELECT p.element_id, p.web_name, p.first_name, p.second_name,
                   ps.now_cost
            FROM players p
            JOIN teams t ON p.team_id = t.team_id
            JOIN player_snapshots ps ON p.element_id = ps.element_id
            WHERE t.short_name = ?
            AND ps.snapshot_id = (SELECT MAX(snapshot_id) FROM snapshots)
            AND ps.now_cost BETWEEN ? AND ?
            ORDER BY p.web_name
        """, (csv_team, price_min, price_max)).fetchall()

        if not candidates:
            # Widen search — maybe price changed a lot
            candidates = conn.execute("""
                SELECT p.element_id, p.web_name, p.first_name, p.second_name,
                       ps.now_cost
                FROM players p
                JOIN teams t ON p.team_id = t.team_id
                JOIN player_snapshots ps ON p.element_id = ps.element_id
                WHERE t.short_name = ?
                AND ps.snapshot_id = (SELECT MAX(snapshot_id) FROM snapshots)
                ORDER BY p.web_name
            """, (csv_team,)).fetchall()

        candidate_dicts = [
            {'id': c[0], 'web_name': c[1], 'first_name': c[2],
             'second_name': c[3], 'price': c[4]}
            for c in candidates
        ]

        element_id = resolve_player(csv_name, csv_team, csv_pos, csv_price, candidate_dicts)

        if element_id:
            resolved[(csv_name, csv_team)] = element_id
            match_name = next(c['web_name'] for c in candidate_dicts if c['id'] == element_id)
            print(f"  [AI] ✓ '{csv_name}' ({csv_team}) → {match_name} (ID {element_id})")
        else:
            print(f"  [AI] ✗ '{csv_name}' ({csv_team}) → could not resolve")

    return resolved
