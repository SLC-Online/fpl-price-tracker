#!/usr/bin/env python3
"""
Build a unified SQLite database from:
1. Vaastav FPL historical data (per-player per-GW stats, prices, points)
2. Scraped Transfer Algorithm CSVs (the creator's projections)
3. Scraped 'my team' posts (the creator's actual decisions)

Output: fpl_project/data/fpl_database.db
"""
import csv
import json
import os
import re
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
VAASTAV = BASE / "external_data" / "vaastav_fpl" / "data"
RAW = BASE / "data" / "raw"
DB_PATH = BASE / "data" / "fpl_database.db"

SEASONS = ["2022-23", "2023-24", "2024-25", "2025-26"]


def create_schema(conn):
    """Create all tables."""
    conn.executescript("""
    -- Players: canonical identity across seasons
    -- NOTE: element_id is only unique WITHIN a season, not across seasons
    CREATE TABLE IF NOT EXISTS players (
        element_id INTEGER NOT NULL,  -- FPL element_id (season-specific)
        season TEXT NOT NULL,
        web_name TEXT,
        first_name TEXT,
        second_name TEXT,
        position TEXT,  -- GK/DEF/MID/FWD
        team TEXT,
        start_price REAL,  -- price at season start (in £m)
        PRIMARY KEY(season, element_id)
    );

    -- Per-player per-GW actual outcomes
    CREATE TABLE IF NOT EXISTS player_gw (
        season TEXT NOT NULL,
        gw INTEGER NOT NULL,
        element INTEGER NOT NULL,  -- FPL element_id
        name TEXT,
        position TEXT,
        team TEXT,
        opponent_team INTEGER,
        was_home INTEGER,
        minutes INTEGER,
        starts INTEGER,
        total_points INTEGER,
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
        value REAL,  -- price in £m at this GW
        selected INTEGER,  -- number of managers selecting
        transfers_in INTEGER,
        transfers_out INTEGER,
        transfers_balance INTEGER,
        expected_goals REAL,
        expected_assists REAL,
        expected_goal_involvements REAL,
        expected_goals_conceded REAL,
        kickoff_time TEXT,
        fixture INTEGER,
        xP REAL,  -- vaastav's xP column
        -- DEFCON (2025-26+)
        clearances_blocks_interceptions INTEGER,
        defensive_contribution INTEGER,
        recoveries INTEGER,
        tackles INTEGER,
        PRIMARY KEY(season, gw, element, fixture)
    );

    -- Transfer Algorithm projections (from scraped CSVs)
    CREATE TABLE IF NOT EXISTS algorithm_projections (
        season TEXT NOT NULL,
        gw INTEGER NOT NULL,
        rank INTEGER,  -- the "No." column
        bcv REAL,
        position TEXT,
        player_name TEXT,  -- raw name from the CSV
        team TEXT,
        price REAL,
        weighted_minutes REAL,
        weighted_uppm REAL,
        ppg_longer_term REAL,
        fixture_ratio REAL,
        gw_projection REAL,  -- the projection for THIS GW
        sum_projection REAL,  -- the multi-week sum
        PRIMARY KEY(season, gw, player_name)
    );

    -- The creator's actual decisions
    CREATE TABLE IF NOT EXISTS decisions (
        season TEXT NOT NULL,
        gw INTEGER NOT NULL,
        rank_before TEXT,
        rank_after TEXT,
        chips_status TEXT,
        ft_available INTEGER,
        money_itb REAL,
        transfers_made TEXT,  -- JSON: [{"in": "X", "out": "Y"}, ...]
        captain TEXT,
        vice_captain TEXT,
        chip_played TEXT,  -- wildcard/bench_boost/triple_captain/free_hit/none
        bench_order TEXT,  -- JSON list
        reasoning TEXT,  -- full text of considerations
        raw_text TEXT,  -- the full 'my team' post text
        PRIMARY KEY(season, gw)
    );

    -- Indices for fast queries
    CREATE INDEX IF NOT EXISTS idx_pgw_season_gw ON player_gw(season, gw);
    CREATE INDEX IF NOT EXISTS idx_pgw_element ON player_gw(element, season);
    CREATE INDEX IF NOT EXISTS idx_pgw_name ON player_gw(name, season);
    CREATE INDEX IF NOT EXISTS idx_algo_season_gw ON algorithm_projections(season, gw);
    """)
    conn.commit()


def load_vaastav_season(conn, season):
    """Load a season's merged_gw.csv into player_gw table."""
    merged = VAASTAV / season / "gws" / "merged_gw.csv"
    if not merged.exists():
        print(f"  [skip] {merged} not found")
        return 0

    count = 0
    with open(merged, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            try:
                rec = {
                    "season": season,
                    "gw": int(row.get("GW") or row.get("round", 0)),
                    "element": int(row.get("element", 0)),
                    "name": row.get("name", ""),
                    "position": row.get("position", ""),
                    "team": row.get("team", ""),
                    "opponent_team": int(row.get("opponent_team", 0) or 0),
                    "was_home": 1 if row.get("was_home", "").lower() == "true" else 0,
                    "minutes": int(row.get("minutes", 0) or 0),
                    "starts": int(row.get("starts", 0) or 0),
                    "total_points": int(row.get("total_points", 0) or 0),
                    "goals_scored": int(row.get("goals_scored", 0) or 0),
                    "assists": int(row.get("assists", 0) or 0),
                    "clean_sheets": int(row.get("clean_sheets", 0) or 0),
                    "goals_conceded": int(row.get("goals_conceded", 0) or 0),
                    "own_goals": int(row.get("own_goals", 0) or 0),
                    "penalties_saved": int(row.get("penalties_saved", 0) or 0),
                    "penalties_missed": int(row.get("penalties_missed", 0) or 0),
                    "yellow_cards": int(row.get("yellow_cards", 0) or 0),
                    "red_cards": int(row.get("red_cards", 0) or 0),
                    "saves": int(row.get("saves", 0) or 0),
                    "bonus": int(row.get("bonus", 0) or 0),
                    "bps": int(row.get("bps", 0) or 0),
                    "value": float(row.get("value", 0) or 0) / 10.0,  # Convert to £m
                    "selected": int(row.get("selected", 0) or 0),
                    "transfers_in": int(row.get("transfers_in", 0) or 0),
                    "transfers_out": int(row.get("transfers_out", 0) or 0),
                    "transfers_balance": int(row.get("transfers_balance", 0) or 0),
                    "expected_goals": float(row.get("expected_goals", 0) or 0),
                    "expected_assists": float(row.get("expected_assists", 0) or 0),
                    "expected_goal_involvements": float(row.get("expected_goal_involvements", 0) or 0),
                    "expected_goals_conceded": float(row.get("expected_goals_conceded", 0) or 0),
                    "kickoff_time": row.get("kickoff_time", ""),
                    "fixture": int(row.get("fixture", 0) or 0),
                    "xP": float(row.get("xP", 0) or 0),
                    "clearances_blocks_interceptions": int(row.get("clearances_blocks_interceptions", 0) or 0),
                    "defensive_contribution": int(row.get("defensive_contribution", 0) or 0),
                    "recoveries": int(row.get("recoveries", 0) or 0),
                    "tackles": int(row.get("tackles", 0) or 0),
                }
                batch.append(rec)
                count += 1
            except (ValueError, TypeError) as e:
                continue  # Skip malformed rows

            if len(batch) >= 1000:
                _insert_batch(conn, "player_gw", batch)
                batch = []

        if batch:
            _insert_batch(conn, "player_gw", batch)

    return count


def _insert_batch(conn, table, records):
    """Insert a batch of dicts into a table, ignoring conflicts."""
    if not records:
        return
    cols = list(records[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    col_str = ",".join(cols)
    conn.executemany(
        f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({placeholders})",
        [tuple(r[c] for c in cols) for r in records]
    )
    conn.commit()


def load_players(conn, season):
    """Load player identity list for a season."""
    idlist = VAASTAV / season / "player_idlist.csv"
    if not idlist.exists():
        return

    players_raw = VAASTAV / season / "players_raw.csv"
    # Build a lookup of id -> (position, team, price) from players_raw
    extra = {}
    if players_raw.exists():
        with open(players_raw, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    eid = int(row.get("id", 0))
                    pos_map = {"1": "GK", "2": "DEF", "3": "MID", "4": "FWD"}
                    extra[eid] = {
                        "position": pos_map.get(str(row.get("element_type", "")), ""),
                        "team": row.get("team", ""),
                        "web_name": row.get("web_name", ""),
                        "start_price": (float(row.get("now_cost", 0) or 0)
                                        - float(row.get("cost_change_start", 0) or 0)) / 10.0,
                    }
                except (ValueError, TypeError):
                    continue

    with open(idlist, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            eid = int(row.get("id", 0))
            info = extra.get(eid, {})
            conn.execute(
                """INSERT OR IGNORE INTO players
                   (element_id, season, web_name, first_name, second_name, position, team, start_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (eid, season, info.get("web_name", row.get("second_name", "")),
                 row.get("first_name", ""), row.get("second_name", ""),
                 info.get("position", ""), info.get("team", ""),
                 info.get("start_price", 0))
            )
    conn.commit()


def parse_algorithm_csv(filepath, season, gw):
    """Parse a Transfer Algorithm CSV (handles both comma and semicolon delimiters)."""
    raw = filepath.read_bytes()

    # Detect delimiter
    first_line = raw.split(b'\n')[0].decode('utf-8', errors='replace')
    if ';' in first_line and ',' not in first_line.replace('",', ''):
        delimiter = ';'
    else:
        delimiter = ','

    # Detect encoding
    try:
        text = raw.decode('utf-8-sig')  # handles BOM
    except UnicodeDecodeError:
        text = raw.decode('latin-1')

    rows = []
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    header = None
    for i, row in enumerate(reader):
        if i == 0:
            # Normalise header
            header = [h.strip().lower() for h in row]
            continue
        if len(row) < 6:
            continue

        try:
            # Find key columns by header
            no_idx = next((j for j, h in enumerate(header) if h.startswith("no")), 0)
            bcv_idx = next((j for j, h in enumerate(header) if "bcv" in h), 1)
            pos_idx = next((j for j, h in enumerate(header) if "pos" in h), 2)
            player_idx = next((j for j, h in enumerate(header) if "player" in h), 3)
            team_idx = next((j for j, h in enumerate(header) if "team" in h), 4)
            price_idx = next((j for j, h in enumerate(header) if "price" in h), 5)
            wmin_idx = next((j for j, h in enumerate(header) if "weighted min" in h or "w min" in h), 6)
            uppm_idx = next((j for j, h in enumerate(header) if "uppm" in h), 7)
            ppg_idx = next((j for j, h in enumerate(header) if "ppg" in h), 8)
            fix_idx = next((j for j, h in enumerate(header) if "fixture" in h), 9)

            # The GW-specific projection column: look for column matching current GW number
            gw_col_idx = None
            for j, h in enumerate(header):
                if h.strip() == str(gw):
                    gw_col_idx = j
                    break

            # Sum column is typically the last
            sum_idx = next((j for j, h in enumerate(header) if "sum" in h), len(header) - 1)

            def clean_num(s):
                s = s.strip().replace('(', '-').replace(')', '').replace('%', '').replace(' ', '')
                if s in ('-', '', '—', '–'):
                    return None
                return float(s)

            rec = {
                "season": season,
                "gw": gw,
                "rank": int(row[no_idx].strip()) if row[no_idx].strip().isdigit() else i,
                "bcv": clean_num(row[bcv_idx]) if bcv_idx < len(row) else None,
                "position": row[pos_idx].strip() if pos_idx < len(row) else "",
                "player_name": row[player_idx].strip() if player_idx < len(row) else "",
                "team": row[team_idx].strip() if team_idx < len(row) else "",
                "price": clean_num(row[price_idx]) if price_idx < len(row) else None,
                "weighted_minutes": clean_num(row[wmin_idx]) if wmin_idx < len(row) else None,
                "weighted_uppm": clean_num(row[uppm_idx]) if uppm_idx < len(row) else None,
                "ppg_longer_term": clean_num(row[ppg_idx]) if ppg_idx < len(row) else None,
                "fixture_ratio": clean_num(row[fix_idx]) if fix_idx < len(row) else None,
                "gw_projection": clean_num(row[gw_col_idx]) if gw_col_idx and gw_col_idx < len(row) else None,
                "sum_projection": clean_num(row[sum_idx]) if sum_idx < len(row) else None,
            }
            if rec["player_name"]:
                rows.append(rec)
        except (ValueError, IndexError, StopIteration):
            continue

    return rows


def load_algorithm_csvs(conn):
    """Load all Transfer Algorithm CSVs into the database."""
    total = 0
    for season in SEASONS:
        season_dir = RAW / season
        if not season_dir.exists():
            continue
        for gw_dir in sorted(season_dir.iterdir()):
            if not gw_dir.is_dir() or not gw_dir.name.startswith("gw"):
                continue
            gw = int(gw_dir.name[2:])
            csv_path = gw_dir / "TransferAlgorithm.csv"
            if not csv_path.exists():
                continue

            rows = parse_algorithm_csv(csv_path, season, gw)
            if rows:
                _insert_batch(conn, "algorithm_projections", rows)
                total += len(rows)

    return total


def parse_my_team_text(text):
    """Extract structured fields from a 'my team' post."""
    result = {
        "rank_before": None,
        "rank_after": None,
        "chips_status": None,
        "ft_available": None,
        "money_itb": None,
        "transfers_made": None,
        "captain": None,
        "vice_captain": None,
        "chip_played": None,
        "bench_order": None,
        "reasoning": "",
    }

    lines = text.split('\n')
    considerations_start = None

    for i, line in enumerate(lines):
        line_s = line.strip()

        # Rank
        rank_m = re.search(r'Rank:\s*([\d,k]+)\s*[-–>→]+\s*([\d,k]+)', line_s, re.I)
        if rank_m:
            result["rank_before"] = rank_m.group(1)
            result["rank_after"] = rank_m.group(2)

        # Chips
        chips_m = re.search(r'Chips?:\s*(.+)', line_s, re.I)
        if chips_m:
            result["chips_status"] = chips_m.group(1).strip()

        # FT
        ft_m = re.search(r'FT:\s*(\d+)', line_s, re.I)
        if ft_m:
            result["ft_available"] = int(ft_m.group(1))

        # Money ITB
        itb_m = re.search(r'(?:ITB|Money|Bank):\s*[£$]?([\d.]+)', line_s, re.I)
        if itb_m:
            result["money_itb"] = float(itb_m.group(1))

        # Captain — look for explicit patterns
        cap_m = re.search(r'(?:^|\s)(\w[\w.-]+)\s+C\b', line_s)
        if not cap_m:
            cap_m = re.search(r'\b([A-Z][\w.-]+)\s*\(?\s*C\s*\)?', line_s)
        if not cap_m:
            cap_m = re.search(r'[Cc]aptain(?:cy)?[:\s]+([A-Z][\w.-]+)', line_s)
        if cap_m and len(cap_m.group(1)) < 25:
            result["captain"] = cap_m.group(1).strip()

        # VC
        vc_m = re.search(r'\bVC[:\s]+([A-Z][\w.-]+)', line_s)
        if not vc_m:
            vc_m = re.search(r'([A-Z][\w.-]+)\s+VC\b', line_s)
        if vc_m and len(vc_m.group(1)) < 25:
            result["vice_captain"] = vc_m.group(1).strip()

        # Chip played
        for chip in ["wildcard", "bench boost", "triple captain", "free hit"]:
            if chip in line_s.lower() and ("played" in line_s.lower() or "activated" in line_s.lower() or "active" in line_s.lower()):
                result["chip_played"] = chip.replace(" ", "_")

        # Transfers
        transfer_m = re.findall(r'(\w[\w\s.-]+?)\s*(?:for|→|->|to)\s*(\w[\w\s.-]+)', line_s)
        if transfer_m and "transfer" in line_s.lower():
            result["transfers_made"] = json.dumps([{"in": a.strip(), "out": b.strip()} for a, b in transfer_m])

        # Considerations section
        if "consideration" in line_s.lower():
            considerations_start = i + 1

    if considerations_start:
        result["reasoning"] = '\n'.join(lines[considerations_start:]).strip()

    return result


def load_decisions(conn):
    """Load all 'my team' posts into the decisions table."""
    total = 0
    for season in SEASONS:
        season_dir = RAW / season
        if not season_dir.exists():
            continue
        for gw_dir in sorted(season_dir.iterdir()):
            if not gw_dir.is_dir() or not gw_dir.name.startswith("gw"):
                continue
            gw = int(gw_dir.name[2:])
            mt_path = gw_dir / "my_team_text.txt"
            if not mt_path.exists():
                continue

            text = mt_path.read_text(encoding="utf-8", errors="replace")
            parsed = parse_my_team_text(text)

            conn.execute(
                """INSERT OR REPLACE INTO decisions
                   (season, gw, rank_before, rank_after, chips_status, ft_available,
                    money_itb, transfers_made, captain, vice_captain, chip_played,
                    bench_order, reasoning, raw_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season, gw, parsed["rank_before"], parsed["rank_after"],
                 parsed["chips_status"], parsed["ft_available"], parsed["money_itb"],
                 parsed["transfers_made"], parsed["captain"], parsed["vice_captain"],
                 parsed["chip_played"], parsed["bench_order"], parsed["reasoning"], text)
            )
            total += 1

    conn.commit()
    return total


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()  # Fresh build each time

    conn = sqlite3.connect(str(DB_PATH))
    create_schema(conn)

    print("=== Loading vaastav FPL data ===")
    for season in SEASONS:
        print(f"  {season}...", end=" ", flush=True)
        n = load_vaastav_season(conn, season)
        load_players(conn, season)
        print(f"{n:,} player-GW rows")

    print("\n=== Loading Transfer Algorithm CSVs ===")
    n = load_algorithm_csvs(conn)
    print(f"  {n:,} projection rows loaded")

    print("\n=== Loading decisions (my team posts) ===")
    n = load_decisions(conn)
    print(f"  {n} GW decisions loaded")

    print("\n=== Summary ===")
    for table in ["players", "player_gw", "algorithm_projections", "decisions"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {count:,} rows")

    # Quick sanity check
    print("\n=== Sanity checks ===")
    row = conn.execute("""
        SELECT season, gw, name, total_points, value, minutes
        FROM player_gw WHERE name LIKE '%Saka%' AND season='2025-26' AND gw=8
    """).fetchone()
    if row:
        print(f"  Saka GW8 2025-26: {row[3]}pts, £{row[4]}m, {row[5]}min")

    row = conn.execute("""
        SELECT season, gw, player_name, gw_projection, price
        FROM algorithm_projections WHERE player_name LIKE '%Saka%' AND season='2025-26' AND gw=8
    """).fetchone()
    if row:
        print(f"  Algorithm Saka GW8 2025-26: projected {row[3]}, price £{row[4]}")

    row = conn.execute("""
        SELECT season, gw, captain, ft_available, chip_played
        FROM decisions WHERE season='2025-26' AND gw=38
    """).fetchone()
    if row:
        print(f"  Decision GW38 2025-26: captain={row[2]}, FT={row[3]}, chip={row[4]}")

    conn.close()
    print(f"\n[done] Database: {DB_PATH} ({DB_PATH.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
