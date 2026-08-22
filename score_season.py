#!/usr/bin/env python3
"""
Score the Transfer Algorithm Patreon creator's 2025-26 FPL season.
Uses the player_gw table from the database for actual points.
Implements: captain bonus, triple captain, bench boost, free hit, auto-subs, transfer costs.
"""

import sqlite3
import json

DB_PATH = "data/fpl_database.db"
SEASON = "2025-26"

def get_player_gw_data(conn, gw, elements):
    """Get points and minutes for a list of element IDs in a given GW.
    Handles DGWs by summing points and taking max minutes."""
    placeholders = ",".join(str(e) for e in elements)
    cursor = conn.execute(f"""
        SELECT element, name, SUM(total_points) as total_points, MAX(minutes) as minutes, position
        FROM player_gw 
        WHERE season='{SEASON}' AND gw={gw} AND element IN ({placeholders})
        GROUP BY element
    """)
    data = {}
    for row in cursor:
        data[row[0]] = {
            "element": row[0],
            "name": row[1],
            "total_points": row[2],
            "minutes": row[3],
            "position": row[4]
        }
    return data

def auto_sub(starting_xi, bench, player_data):
    """
    Perform auto-substitutions.
    Rules:
    - If a starting player got 0 minutes, they can be subbed out
    - Bench players come on in order (bench[0], bench[1], bench[2])
    - Formation must remain valid: at least 1 GK, 3 DEF, 2 MID, 1 FWD in final XI
    - GK sub: only GK replaces GK (bench GK is separate)
    """
    final_xi = list(starting_xi)
    used_bench = set()
    
    # Count positions in starting XI
    def count_positions(xi):
        counts = {"GK": 0, "DEF": 0, "MID": 0, "FWD": 0}
        for e in xi:
            if e in player_data:
                pos = player_data[e]["position"]
                counts[pos] = counts.get(pos, 0) + 1
        return counts
    
    # Process outfield subs (bench positions 0, 1, 2 are outfield)
    outfield_bench = [b for b in bench if b in player_data and player_data[b]["position"] not in ("GKP", "GK")]
    bench_gk = [b for b in bench if b in player_data and player_data[b]["position"] in ("GKP", "GK")]
    
    for i, starter in enumerate(final_xi):
        if starter not in player_data:
            continue
        if player_data[starter]["minutes"] > 0:
            continue
        if player_data[starter]["position"] in ("GKP", "GK"):
            # GK auto-sub: only bench GK can replace
            for bg in bench_gk:
                if bg in used_bench:
                    continue
                if bg in player_data and player_data[bg]["minutes"] > 0:
                    final_xi[i] = bg
                    used_bench.add(bg)
                    break
        else:
            # Outfield auto-sub
            starter_pos = player_data[starter]["position"]
            for sub in outfield_bench:
                if sub in used_bench:
                    continue
                if sub not in player_data or player_data[sub]["minutes"] == 0:
                    continue
                sub_pos = player_data[sub]["position"]
                # Check if substitution maintains valid formation
                # Temporarily make the swap and check
                test_xi = list(final_xi)
                test_xi[i] = sub
                counts = count_positions(test_xi)
                if counts.get("DEF", 0) >= 3 and counts.get("MID", 0) >= 2 and counts.get("FWD", 0) >= 1:
                    final_xi[i] = sub
                    used_bench.add(sub)
                    break
    
    return final_xi

def score_gw(conn, gw, starting_xi, bench, captain, is_tc=False, is_bb=False):
    """
    Score a single gameweek.
    Returns (total_points, details_string)
    """
    all_players = list(set(starting_xi + bench))
    player_data = get_player_gw_data(conn, gw, all_players)
    
    if is_bb:
        # Bench Boost: all 15 players score
        total = 0
        for e in all_players:
            if e in player_data:
                pts = player_data[e]["total_points"]
                if e == captain:
                    pts *= 2  # Captain still gets double
                    if is_tc:
                        pts = player_data[e]["total_points"] * 3  # TC = triple
                total += pts
            else:
                pass  # Player not found - 0 points
        return total
    
    # Normal week or TC week
    # Do auto-subs
    final_xi = auto_sub(starting_xi, bench, player_data)
    
    total = 0
    for e in final_xi:
        if e in player_data:
            pts = player_data[e]["total_points"]
            if e == captain:
                if is_tc:
                    pts *= 3  # Triple captain
                else:
                    pts *= 2  # Normal captain (double points)
            total += pts
    
    return total

# ============================================================
# COMPLETE GW-BY-GW DATA
# ============================================================

# Format: (squad_15, starting_xi_11, bench_4, captain, chip, transfer_cost)
# bench is ordered: [GK, sub1, sub2, sub3]
# chip: None, "WC", "BB", "TC", "FH"

gw_data = {}

# GW1: {139,568,317,575,48,381,235,16,267,283,64,470,441,191,252}
# Cap=Salah(381)
# Need to determine starting XI from screenshots - using typical formation
# From context: this was tracked from screenshots
gw_data[1] = {
    "starting_xi": [139, 568, 317, 575, 48, 381, 235, 16, 267, 283, 64],
    "bench": [470, 441, 191, 252],
    "captain": 381,
    "chip": None,
    "transfer_cost": 0
}

# GW2: same squad. Cap=Saka(16)
gw_data[2] = {
    "starting_xi": [470, 441, 317, 191, 48, 381, 235, 16, 267, 64, 283],
    "bench": [139, 568, 575, 252],
    "captain": 16,
    "chip": None,
    "transfer_cost": 0
}

# GW3: Transfers: Saka(16)->Semenyo(82), Palmer(235)->Bruno(449). 2 transfers, 2 FT = 0
# Verbruggen, Porro, Dorgu, VdV, Tielemans, Salah, Bruno(C), Sarr, Semenyo, Watkins, Mateta
# Bench: Dubravka, Andersen, Esteve, Guiu. VC=Salah
gw_data[3] = {
    "starting_xi": [139, 568, 441, 575, 48, 381, 449, 267, 82, 64, 283],
    "bench": [470, 317, 191, 252],
    "captain": 449,
    "chip": None,
    "transfer_cost": 0
}

# GW4: Transfer: Dorgu(441)->VVD(373). 1 transfer, 1 FT = 0
# Verbruggen, Porro, Andersen, VdV, Virgil, Tielemans, Salah(C), Bruno, Semenyo, Watkins, Mateta(VC)
# Bench: Dubravka, Esteve, Guiu, Sarr
gw_data[4] = {
    "starting_xi": [139, 568, 317, 575, 373, 48, 381, 449, 82, 64, 283],
    "bench": [470, 191, 252, 267],
    "captain": 381,
    "chip": None,
    "transfer_cost": 0
}

# GW5: Transfer: Tielemans(48)->Wirtz(382). 1 transfer, 1 FT = 0
# Dubravka, Esteve, Porro, Andersen, Virgil, Wirtz, Salah(C), Bruno(VC), Semenyo, Watkins, Mateta
# Bench: Verbruggen, VdV, Guiu, Sarr
gw_data[5] = {
    "starting_xi": [470, 191, 568, 317, 373, 382, 381, 449, 82, 64, 283],
    "bench": [139, 575, 252, 267],
    "captain": 381,
    "chip": None,
    "transfer_cost": 0
}

# GW6 WC: {67,291,5,72,237,16,427,449,82,249,430,470,691,317,348}
# Cap=Haaland(430). Wildcard = 0 cost.
gw_data[6] = {
    "starting_xi": [67, 291, 5, 72, 16, 427, 449, 82, 249, 430, 237],
    "bench": [470, 691, 317, 348],
    "captain": 430,
    "chip": "WC",
    "transfer_cost": 0
}

# GW7 BB: same squad. Cap=Haaland(430). All 15 score.
gw_data[7] = {
    "starting_xi": [67, 291, 5, 72, 16, 427, 449, 82, 249, 430, 237],
    "bench": [470, 691, 317, 348],
    "captain": 430,
    "chip": "BB",
    "transfer_cost": 0
}

# GW8: same squad. Cap=Haaland(430)
# Dubravka, Rodon, Gabriel, Senesi, Reijnders, Saka(VC), Bruno, Semenyo, DCL, JP, Haaland(C)
# Bench: Petrovic, Enzo, Andersen, Tarkowski
gw_data[8] = {
    "starting_xi": [470, 348, 5, 72, 427, 16, 449, 82, 691, 249, 430],
    "bench": [67, 237, 317, 291],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW9: same squad. Cap=Haaland(430)
# Petrovic, Rodon, Gabriel, Senesi, Enzo, Saka(VC), Bruno, Semenyo, DCL, JP, Haaland(C)
# Bench: Dubravka, Tarkowski, Reijnders, Andersen
gw_data[9] = {
    "starting_xi": [67, 348, 5, 72, 237, 16, 449, 82, 691, 249, 430],
    "bench": [470, 291, 427, 317],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW10: JP(249)->Mateta(283), Reijnders(427)->Sarr(267), DCL(691)->Guiu(252). 3 transfers, 4 FT = 0
# Dubravka, Tarkowski, Gabriel, Andersen, Enzo, Saka(VC), Bruno, Semenyo, Sarr, Mateta, Haaland(C)
# Bench: Petrovic, Rodon, Senesi, Guiu
gw_data[10] = {
    "starting_xi": [470, 291, 5, 317, 237, 16, 449, 82, 267, 283, 430],
    "bench": [67, 348, 72, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW11: same. Cap=Haaland(430)
# Petrovic, Tarkowski, Gabriel, Andersen, Enzo, Saka(VC), Bruno, Semenyo, Sarr, Haaland(C), Mateta
# Bench: Dubravka, Rodon, Senesi, Guiu
gw_data[11] = {
    "starting_xi": [67, 291, 5, 317, 237, 16, 449, 82, 267, 430, 283],
    "bench": [470, 348, 72, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW12: Gabriel(5)->Virgil(373). 1 transfer, 1 FT = 0
# Petrovic, Andersen, Virgil, Senesi, Enzo, Saka(VC), Bruno, Semenyo, Sarr, Haaland(C), Mateta
# Bench: Dubravka, Rodon, Tarkowski, Guiu
gw_data[12] = {
    "starting_xi": [67, 317, 373, 72, 237, 16, 449, 82, 267, 430, 283],
    "bench": [470, 348, 291, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW13 TC: Mateta(283)->Thiago(136). 1 transfer, 1 FT = 0
# Squad: {470,291,373,317,237,16,449,82,267,136,430,67,348,72,252}
# Cap=Haaland(430) TRIPLE CAPTAIN
gw_data[13] = {
    "starting_xi": [67, 291, 373, 72, 16, 449, 82, 267, 136, 430, 237],
    "bench": [470, 317, 348, 252],
    "captain": 430,
    "chip": "TC",
    "transfer_cost": 0
}

# GW14 FH: {366,257,373,256,8,119,16,449,381,517,283,470,191,252,467}
# Cap=Salah(381). Free Hit = 0 cost.
gw_data[14] = {
    "starting_xi": [366, 257, 373, 256, 8, 16, 449, 381, 517, 283, 119],
    "bench": [470, 191, 252, 467],
    "captain": 381,
    "chip": "FH",
    "transfer_cost": 0
}

# GW15: reverts to GW13 squad, then Sarr(267)->Foden(414), Senesi(72)->Thiaw(684), Petro(67)->Verbruggen(139)
# 3 transfers, 2 FT = -4 cost
# Squad: {139,291,373,684,237,16,449,82,414,430,136,470,317,348,252}
gw_data[15] = {
    "starting_xi": [139, 291, 373, 684, 16, 449, 82, 414, 430, 136, 237],
    "bench": [470, 317, 348, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW16: Thiaw(684)->Hincapié(725). 1 transfer, 1 FT = 0
# Dubravka, Virgil, Hincapie, Andersen, Saka(C), Bruno(VC), Semenyo, Foden, Enzo, Thiago, Haaland
# Bench: Verbruggen, Rodon, Tarkowski, Guiu
gw_data[16] = {
    "starting_xi": [470, 373, 725, 317, 16, 449, 82, 414, 237, 136, 430],
    "bench": [139, 348, 291, 252],
    "captain": 16,
    "chip": None,
    "transfer_cost": 0
}

# GW17: Virgil(373)->O'Reilly(411). 1 transfer, 1 FT = 0
# Squad: {139,725,411,317,237,16,449,82,414,136,430,470,348,291,252}
# Cap=Haaland(430)
gw_data[17] = {
    "starting_xi": [139, 725, 411, 317, 16, 449, 82, 414, 136, 430, 237],
    "bench": [470, 291, 348, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW18: Hincapié(725)->Virgil(373), Bruno(449)->Cunha(450). 2 transfers, 5 FT = 0
# Dubravka, Virgil, O'Reilly, Tarkowski, Saka(VC), Cunha, Semenyo, Foden, Enzo, Thiago, Haaland(C)
# Bench: Verbruggen, Andersen, Rodon, Guiu
gw_data[18] = {
    "starting_xi": [470, 373, 411, 291, 16, 450, 82, 414, 237, 136, 430],
    "bench": [139, 317, 348, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW19: Semenyo(82)->Gordon(485), Rodon(348)->Thiaw(684). 2 transfers, 4 FT = 0
# Verbruggen, Virgil, O'Reilly, Thiaw, Saka, Cunha(VC), Gordon, Foden, Enzo, Thiago, Haaland(C)
# Bench: Dubravka, Tarkowski, Andersen, Guiu
gw_data[19] = {
    "starting_xi": [139, 373, 411, 684, 16, 450, 485, 414, 237, 136, 430],
    "bench": [470, 291, 317, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW20: same squad. Cap=Haaland(430)
# Verbruggen, Thiaw, Tarkowski, Virgil, Gordon, Foden, Enzo, Saka(VC), Cunha, Haaland(C), Thiago
# Bench: Dubravka, Andersen, O'Reilly, Guiu
gw_data[20] = {
    "starting_xi": [139, 684, 291, 373, 485, 414, 237, 16, 450, 430, 136],
    "bench": [470, 317, 411, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW21: same squad. Cap=Haaland(430)
# Dubravka, O'Reilly, Thiaw, Tarkowski, Foden, Saka, Gordon, Cunha(VC), Enzo, Haaland(C), Thiago
# Bench: Verbruggen, Andersen, Virgil, Guiu
gw_data[21] = {
    "starting_xi": [470, 411, 684, 291, 414, 16, 485, 450, 237, 430, 136],
    "bench": [139, 317, 373, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW22: O'Reilly(411)->Gabriel(5), Cunha(450)->Tavernier(84). 2 transfers, 2 FT = 0
# Verbruggen, Virgil, Thiaw, Gabriel, Foden, Saka(VC), Gordon, Tavernier, Enzo, Haaland(C), Thiago
# Bench: Dubravka, Andersen, Tarkowski, Guiu
gw_data[22] = {
    "starting_xi": [139, 373, 684, 5, 414, 16, 485, 84, 237, 430, 136],
    "bench": [470, 317, 291, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW23: Foden(414)->Bruno(449). 1 transfer, 1 FT = 0
# Verbruggen, Virgil, Thiaw, Gabriel, Tarkowski, Bruno, Gordon, Saka, Enzo, Haaland(C), Thiago(VC)
# Bench: Dubravka, Andersen(Fulham), Guiu, Tavernier
gw_data[23] = {
    "starting_xi": [139, 373, 684, 5, 291, 449, 485, 16, 237, 430, 136],
    "bench": [470, 317, 252, 84],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW24: Thiaw(684)->Timber(8), Gordon(485)->Anderson(517). 2 transfers, 4 FT = 0
# Verbruggen, Virgil, Timber, Gabriel, Tarkowski, Bruno(C), Anderson(NF), Saka, Enzo, Haaland(VC), Thiago
# Bench: Dubravka, Andersen(Fulham), Guiu, Tavernier
gw_data[24] = {
    "starting_xi": [139, 373, 8, 5, 291, 449, 517, 16, 237, 430, 136],
    "bench": [470, 317, 252, 84],
    "captain": 449,
    "chip": None,
    "transfer_cost": 0
}

# GW25: Saka(16)->Rice(21). 1 transfer, 1 FT = 0
# Verbruggen, Andersen(Fulham), Timber, Gabriel(VC), Tarkowski, Bruno(C), Anderson(NF), Rice, Enzo, Haaland, Thiago
# Bench: Dubravka, Virgil, Guiu, Tavernier
gw_data[25] = {
    "starting_xi": [139, 317, 8, 5, 291, 449, 517, 21, 237, 430, 136],
    "bench": [470, 373, 252, 84],
    "captain": 449,
    "chip": None,
    "transfer_cost": 0
}

# GW26: Dúbravka(470)->José Sá(628). 1 transfer, 1 FT = 0
# José Sá, Virgil, Timber(VC), Gabriel(C), Tarkowski, Bruno, Anderson(NF), Rice, Enzo, Thiago, Haaland
# Bench: Verbruggen, Andersen(Fulham), Tavernier, Guiu
gw_data[26] = {
    "starting_xi": [628, 373, 8, 5, 291, 449, 517, 21, 237, 136, 430],
    "bench": [139, 317, 84, 252],
    "captain": 5,
    "chip": None,
    "transfer_cost": 0
}

# GW27: no transfer.
# Verbruggen, Virgil, Timber, Gabriel, Andersen(Fulham), Bruno(VC), Anderson(NF), Rice, Enzo, Thiago, Haaland(C)
# Bench: José Sá, Tarkowski, Tavernier, Guiu
gw_data[27] = {
    "starting_xi": [139, 373, 8, 5, 317, 449, 517, 21, 237, 136, 430],
    "bench": [628, 291, 84, 252],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW28: Guiu(252)->Ekitiké(661), Rice(21)->Wilson(329). 2 transfers, 2 FT = 0
# Verbruggen, Virgil, Timber, Gabriel, Andersen(Fulham), Bruno(VC), Anderson(NF), Wilson, Ekitiké, Thiago, Haaland(C)
# Bench: José Sá, Tarkowski, Enzo, Tavernier
gw_data[28] = {
    "starting_xi": [139, 373, 8, 5, 317, 449, 517, 329, 661, 136, 430],
    "bench": [628, 291, 237, 84],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW29: no transfer.
# Verbruggen, Virgil, Gabriel, Timber, Tarkowski, Bruno(VC), Tavernier, Enzo, Ekitiké, Thiago, Haaland(C)
# Bench: José Sá, Andersen(Fulham), Wilson, Anderson(NF)
gw_data[29] = {
    "starting_xi": [139, 373, 5, 8, 291, 449, 84, 237, 661, 136, 430],
    "bench": [628, 317, 329, 517],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW30: no transfer.
# Verbruggen, Virgil, Gabriel, Timber, Bruno(C), Tavernier, Anderson(NF), Enzo, Ekitiké, Thiago(VC), Haaland
# Bench: José Sá, Andersen(Fulham), Wilson, Tarkowski
gw_data[30] = {
    "starting_xi": [139, 373, 5, 8, 449, 84, 517, 237, 661, 136, 430],
    "bench": [628, 317, 329, 291],
    "captain": 449,
    "chip": None,
    "transfer_cost": 0
}

# GW31: Timber(8)->Thiaw(684). 1 transfer, 1 FT = 0
# Verbruggen, Virgil, Thiaw, Andersen(Fulham), Bruno(C), Tavernier, Anderson(NF), Enzo, Wilson(VC), Ekitiké, Thiago
# Bench: José Sá, Haaland, Tarkowski, Gabriel
gw_data[31] = {
    "starting_xi": [139, 373, 684, 317, 449, 84, 517, 237, 329, 661, 136],
    "bench": [628, 430, 291, 5],
    "captain": 449,
    "chip": None,
    "transfer_cost": 0
}

# GW32 WC: {139,411,151,72,449,267,82,235,430,624,249,341,84,343,77}
# Verbruggen, O'Reilly, Van Hecke, Senesi, Bruno(C), Sarr, Semenyo, Palmer, Haaland(VC), Bowen, JP
# Bench: Darlow, Tavernier, Struijk, Hill
gw_data[32] = {
    "starting_xi": [139, 411, 151, 72, 449, 267, 82, 235, 430, 624, 249],
    "bench": [341, 84, 343, 77],
    "captain": 449,
    "chip": "WC",
    "transfer_cost": 0
}

# GW33 BB: Bowen(624)->DCL(691). 1 transfer, 1 FT = 0
# Darlow, O'Reilly, Hill, Senesi, Struijk, Semenyo, Tavernier, Palmer(VC), Haaland(C), DCL, JP
# Bench (all score): Verbruggen, Van Hecke, Bruno, Sarr
gw_data[33] = {
    "starting_xi": [341, 411, 77, 72, 343, 82, 84, 235, 430, 691, 249],
    "bench": [139, 151, 449, 267],
    "captain": 430,
    "chip": "BB",
    "transfer_cost": 0
}

# GW34 FH:
# Raya, Virgil, Gabriel, Porro, Bruno(C), Rice, Salah(VC), Wilson, Szoboszlai, Solanke, Bowen
# Bench: Hermansen, Danso, Brobbey, Diouf
gw_data[34] = {
    "starting_xi": [1, 373, 5, 568, 449, 21, 381, 329, 387, 596, 624],
    "bench": [679, 570, 730, 603],
    "captain": 449,
    "chip": "FH",
    "transfer_cost": 0
}

# GW35: Missed deadline. Reverts to GW33 squad, same lineup as GW33.
# Darlow, O'Reilly, Hill, Senesi, Struijk, Semenyo, Tavernier, Palmer, Haaland, DCL, JP
# Bench: Verbruggen, Van Hecke, Bruno, Sarr
# Captain defaults to GW33 captain = Haaland(430)
gw_data[35] = {
    "starting_xi": [341, 411, 77, 72, 343, 82, 84, 235, 430, 691, 249],
    "bench": [139, 151, 449, 267],
    "captain": 430,
    "chip": None,
    "transfer_cost": 0
}

# GW36 TC: Struijk(343)->Gabriel(5), Semenyo(82)->Cherki(417). 2 transfers, 2FT = 0
# Verbruggen, O'Reilly, Gabriel, Van Hecke, Bruno, Tavernier, Palmer, Cherki(VC), Sarr, JP, Haaland(C/TC)
# Bench: Darlow, DCL, Senesi, Hill
gw_data[36] = {
    "starting_xi": [139, 411, 5, 151, 449, 84, 235, 417, 267, 249, 430],
    "bench": [341, 691, 72, 77],
    "captain": 430,
    "chip": "TC",
    "transfer_cost": 0
}

# GW37: Palmer(235)->Saka(16), Sarr(267)->Ndiaye(299), Tavernier(84)->Trossard(20). 3 transfers, 4FT = 0
# Verbruggen, Gabriel, O'Reilly, Van Hecke, Bruno(VC), Trossard, Saka(C), Ndiaye, DCL, JP, Haaland
# Bench: Darlow, Cherki, Senesi, Hill
gw_data[37] = {
    "starting_xi": [139, 5, 411, 151, 449, 20, 16, 299, 691, 249, 430],
    "bench": [341, 417, 72, 77],
    "captain": 16,
    "chip": None,
    "transfer_cost": 0
}

# GW38: Haaland(430)->Bowen(624), Trossard(20)->Marmoush(413). 2 transfers, 2FT = 0
# Bowen(C), with Saka, Cherki, O'Reilly on bench
# Starting: Verbruggen, Gabriel, Senesi, Hill, Van Hecke, Bruno, Marmoush, Ndiaye, DCL, JP, Bowen(C)
# Bench: Darlow, Saka, Cherki, O'Reilly
gw_data[38] = {
    "starting_xi": [139, 5, 72, 77, 151, 449, 413, 299, 691, 249, 624],
    "bench": [341, 16, 417, 411],
    "captain": 624,
    "chip": None,
    "transfer_cost": 0
}


# Vice-captain data (from screenshots/text)
# Format: {gw: vice_captain_element}
vice_captains = {
    1: 235,   # Palmer
    2: 381,   # Salah
    3: 381,   # Salah
    4: 283,   # Mateta
    5: 449,   # Bruno
    6: 449,   # Bruno
    7: 449,   # Bruno
    8: 16,    # Saka
    9: 16,    # Saka
    10: 16,   # Saka
    11: 16,   # Saka
    12: 16,   # Saka
    13: 136,  # Thiago
    14: 449,  # Bruno
    15: 449,  # Bruno
    16: 449,  # Bruno
    17: 414,  # Foden
    18: 16,   # Saka
    19: 450,  # Cunha
    20: 16,   # Saka
    21: 450,  # Cunha
    22: 16,   # Saka
    23: 136,  # Thiago
    24: 430,  # Haaland
    25: 5,    # Gabriel
    26: 8,    # Timber
    27: 449,  # Bruno
    28: 449,  # Bruno
    29: 449,  # Bruno
    30: 136,  # Thiago
    31: 329,  # Wilson
    32: 430,  # Haaland
    33: 235,  # Palmer
    34: 381,  # Salah
    35: 430,  # Haaland (default from GW33)
    36: 417,  # Cherki
    37: 449,  # Bruno
    38: 449,  # Bruno (assumed)
}


def main():
    conn = sqlite3.connect(DB_PATH)
    
    season_total = 0
    total_transfer_cost = 0
    gw_scores = []
    
    print(f"{'GW':<4} {'Raw':>5} {'Cap Bonus':>10} {'TC/BB':>6} {'Transfers':>10} {'Net':>5} {'Chip':<4}")
    print("-" * 55)
    
    for gw in range(1, 39):
        data = gw_data[gw]
        starting_xi = data["starting_xi"]
        bench = data["bench"]
        captain = data["captain"]
        vice_captain = data.get("vice_captain") or vice_captains.get(gw)
        chip = data["chip"]
        transfer_cost = data["transfer_cost"]
        
        all_players = list(set(starting_xi + bench))
        player_data = get_player_gw_data(conn, gw, all_players)
        
        # Resolve captain: if captain got 0 minutes, VC gets armband
        actual_captain = captain
        if captain in player_data and player_data[captain]["minutes"] == 0:
            if vice_captain and vice_captain in player_data and player_data[vice_captain]["minutes"] > 0:
                actual_captain = vice_captain
        elif captain not in player_data:
            if vice_captain and vice_captain in player_data and player_data[vice_captain]["minutes"] > 0:
                actual_captain = vice_captain
        
        if chip == "BB":
            # All 15 score, captain gets double
            total = 0
            for e in all_players:
                if e in player_data:
                    pts = player_data[e]["total_points"]
                    if e == actual_captain:
                        pts *= 2
                    total += pts
        elif chip == "TC":
            # Normal scoring but captain gets triple
            final_xi = auto_sub(starting_xi, bench, player_data)
            total = 0
            for e in final_xi:
                if e in player_data:
                    pts = player_data[e]["total_points"]
                    if e == actual_captain:
                        pts *= 3
                    total += pts
        else:
            # Normal scoring (including WC, FH which just have different squads)
            final_xi = auto_sub(starting_xi, bench, player_data)
            total = 0
            for e in final_xi:
                if e in player_data:
                    pts = player_data[e]["total_points"]
                    if e == actual_captain:
                        pts *= 2
                    total += pts
        
        net = total - transfer_cost
        season_total += net
        total_transfer_cost += transfer_cost
        
        # Get captain points for display
        cap_base = player_data.get(actual_captain, {}).get("total_points", 0)
        
        chip_str = chip if chip else ""
        print(f"GW{gw:<3} {total:>5} {'(C:'+str(cap_base)+')':>10} {chip_str:>6} {'-'+str(transfer_cost) if transfer_cost else '0':>10} {net:>5}")
        gw_scores.append({"gw": gw, "raw": total, "cost": transfer_cost, "net": net, "chip": chip})
    
    print("-" * 55)
    print(f"SEASON TOTAL: {season_total}")
    print(f"Total transfer costs: {total_transfer_cost}")
    print(f"Total raw points: {season_total + total_transfer_cost}")
    
    conn.close()

if __name__ == "__main__":
    main()
