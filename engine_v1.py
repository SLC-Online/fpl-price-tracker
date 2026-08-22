#!/usr/bin/env python3
"""
FPL Prediction Engine v1 — Transfer Decision Model

Given:
- Current squad (15 players with positions and prices)
- Transfer Algorithm CSV data (BCV for all players)
- FT available
- Contextual variables (chip planning, injuries, etc.)

Predicts:
- How many transfers to make (0 = save, or N)
- Which players to buy/sell

Based on observed rules from the 2025-26 creator:
1. BCV gain is the primary driver for WHICH player to transfer
2. Squad problems (injuries, 0-min players) force transfers
3. FT at cap → spend at least 1
4. Never take hits
5. Save when planning for upcoming chip week
"""

import sqlite3
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

DB_PATH = "data/fpl_database.db"


@dataclass
class Player:
    element: int
    name: str
    team: str
    position: str  # GK, DEF, MID, FWD
    price: float
    bcv: float = 0.0
    projected_sum: float = 0.0
    weighted_mins: float = 0.0
    is_injured: bool = False
    minutes_last_gw: int = 90


@dataclass
class TransferRecommendation:
    out_player: Player
    in_player: Player
    bcv_gain: float
    reason: str


class TransferEngine:
    """Predict transfers using BCV-gain maximization."""
    
    # Thresholds learned from 2025-26 data
    MIN_BCV_GAIN = 0.03          # Minimum gain to justify a transfer
    STRONG_GAIN = 0.10           # Gain that almost always triggers a transfer
    PROBLEM_PLAYER_THRESHOLD = 0  # Minutes in last GW that flags a problem
    
    def __init__(self, season: str = '2025-26'):
        self.conn = sqlite3.connect(DB_PATH)
        self.season = season
    
    def get_algorithm_data(self, gw: int) -> List[Player]:
        """Get all players from Transfer Algorithm for a given GW."""
        cursor = self.conn.execute("""
            SELECT player, team, position, bcv, price, weighted_mins, projected_sum
            FROM transfer_algorithm 
            WHERE season=? AND gw=?
            ORDER BY bcv DESC
        """, (self.season, gw))
        
        players = []
        for row in cursor:
            pos = row[2]
            # Normalize position codes
            if pos in ('M',): pos = 'MID'
            elif pos in ('D',): pos = 'DEF'
            elif pos in ('F',): pos = 'FWD'
            elif pos in ('G',): pos = 'GK'
            
            players.append(Player(
                element=0,  # Not available from this table
                name=row[0],
                team=row[1],
                position=pos,
                price=row[4],
                bcv=row[3],
                weighted_mins=row[5],
                projected_sum=row[6],
            ))
        return players
    
    def find_best_replacement(self, 
                              out_player: Player, 
                              squad: List[Player], 
                              all_players: List[Player],
                              money_itb: float) -> Optional[TransferRecommendation]:
        """Find the best BCV replacement for a given player."""
        
        budget = out_player.price + money_itb
        squad_teams = {}
        for p in squad:
            squad_teams[p.team] = squad_teams.get(p.team, 0) + 1
        
        squad_names = {p.name.lower() for p in squad}
        
        best = None
        best_gain = -999
        
        for candidate in all_players:
            # Must be same position
            if candidate.position != out_player.position:
                continue
            # Must be affordable
            if candidate.price > budget:
                continue
            # Must not already be in squad
            if candidate.name.lower() in squad_names:
                continue
            # Max 3 per team (check if adding this player would exceed)
            team_count = squad_teams.get(candidate.team, 0)
            # If the out_player is from same team, we're freeing a slot
            if out_player.team == candidate.team:
                pass  # No net change in team count
            elif team_count >= 3:
                continue
            
            gain = candidate.bcv - out_player.bcv
            if gain > best_gain:
                best_gain = gain
                best = candidate
        
        if best and best_gain > self.MIN_BCV_GAIN:
            reason = f"BCV gain +{best_gain:.2f}"
            if out_player.weighted_mins == 0 or out_player.is_injured:
                reason = f"Problem player replacement, BCV gain +{best_gain:.2f}"
            return TransferRecommendation(
                out_player=out_player,
                in_player=best,
                bcv_gain=best_gain,
                reason=reason,
            )
        return None
    
    def recommend_transfers(self, 
                           squad: List[Player], 
                           gw: int, 
                           ft_available: int,
                           money_itb: float = 0.0,
                           chip_next_week: Optional[str] = None,
                           just_used_chip: bool = False) -> List[TransferRecommendation]:
        """
        Recommend transfers for a given GW.
        
        Returns list of recommended transfers (may be empty = save).
        """
        if ft_available == 0:
            return []  # No FTs, can't transfer (or GW1)
        
        # Get algorithm data
        all_players = self.get_algorithm_data(gw)
        if not all_players:
            return []
        
        # If just used WC or FH, typically save
        if just_used_chip:
            return []
        
        # Find all possible transfer gains
        recommendations = []
        remaining_squad = list(squad)
        remaining_money = money_itb
        
        for _ in range(ft_available):
            # Sort remaining squad by BCV ascending (weakest first)
            remaining_squad.sort(key=lambda p: p.bcv)
            
            best_rec = None
            best_gain = self.MIN_BCV_GAIN
            
            for player in remaining_squad:
                rec = self.find_best_replacement(player, remaining_squad, all_players, remaining_money)
                if rec and rec.bcv_gain > best_gain:
                    best_rec = rec
                    best_gain = rec.bcv_gain
            
            if best_rec:
                recommendations.append(best_rec)
                # Update state for next iteration
                remaining_squad = [p for p in remaining_squad if p.name != best_rec.out_player.name]
                remaining_squad.append(best_rec.in_player)
                remaining_money += best_rec.out_player.price - best_rec.in_player.price
                # Remove the incoming player from candidates
                all_players = [p for p in all_players if p.name != best_rec.in_player.name]
            else:
                break  # No more worthwhile transfers
        
        # Decision: should we actually make these transfers or save?
        if not recommendations:
            return []
        
        # If FT at cap (5), always make at least the best one
        if ft_available >= 5 and recommendations:
            return recommendations[:max(1, len(recommendations))]
        
        # If best gain is strong (>= 0.10), make the transfer(s)
        if recommendations[0].bcv_gain >= self.STRONG_GAIN:
            return recommendations
        
        # If planning chip next week, lean towards saving
        if chip_next_week in ('WC', 'FH'):
            return []
        
        # Otherwise, make transfers where gain is positive
        return [r for r in recommendations if r.bcv_gain >= self.MIN_BCV_GAIN]
    
    def close(self):
        self.conn.close()


class CaptainEngine:
    """Predict captain selection using projected points from the algorithm."""
    
    def __init__(self, season: str = '2025-26'):
        self.conn = sqlite3.connect(DB_PATH)
        self.season = season
    
    def recommend_captain(self, squad: List[Player], gw: int) -> Tuple[Player, Player]:
        """
        Recommend captain and vice-captain.
        
        Strategy: Pick the player with highest projected_sum who is in the starting XI.
        In practice, this is almost always Haaland (58% of GWs) or the next premium.
        """
        # Get algorithm data for this GW
        cursor = self.conn.execute("""
            SELECT player, projected_sum FROM transfer_algorithm 
            WHERE season=? AND gw=?
            ORDER BY projected_sum DESC
        """, (self.season, gw))
        
        projections = {}
        for row in cursor:
            projections[row[0].lower()] = row[1]
        
        # Match squad players to projections
        squad_with_proj = []
        for p in squad:
            # Find projection by name matching
            proj = 0
            for algo_name, algo_proj in projections.items():
                if p.name.lower() in algo_name or algo_name in p.name.lower():
                    proj = algo_proj
                    break
                # Try last name
                last = p.name.split()[-1].lower()
                if last in algo_name:
                    proj = algo_proj
                    break
            squad_with_proj.append((p, proj))
        
        # Sort by projection descending
        squad_with_proj.sort(key=lambda x: x[1], reverse=True)
        
        captain = squad_with_proj[0][0] if squad_with_proj else squad[0]
        vice = squad_with_proj[1][0] if len(squad_with_proj) > 1 else squad[1]
        
        return captain, vice
    
    def close(self):
        self.conn.close()


class LineupEngine:
    """Predict starting XI and bench order using projected points."""
    
    def __init__(self, season: str = '2025-26'):
        self.conn = sqlite3.connect(DB_PATH)
        self.season = season
    
    def recommend_lineup(self, squad: List[Player], gw: int) -> Tuple[List[Player], List[Player]]:
        """
        Recommend starting XI and bench.
        
        Strategy: Start the 11 players with highest projected points 
        that form a valid formation (1 GK, 3+ DEF, 2+ MID, 1+ FWD).
        Bench order: highest projected points first (for auto-sub value).
        """
        # Get projections from algorithm
        cursor = self.conn.execute("""
            SELECT player, projected_sum FROM transfer_algorithm 
            WHERE season=? AND gw=?
        """, (self.season, gw))
        
        projections = {}
        for row in cursor:
            projections[row[0].lower()] = row[1]
        
        # Assign projections to squad
        for p in squad:
            p.projected_sum = 0
            for algo_name, proj in projections.items():
                if p.name.split()[-1].lower() in algo_name or algo_name in p.name.lower():
                    p.projected_sum = proj
                    break
        
        # Split by position
        gks = sorted([p for p in squad if p.position == 'GK'], key=lambda p: p.projected_sum, reverse=True)
        defs = sorted([p for p in squad if p.position == 'DEF'], key=lambda p: p.projected_sum, reverse=True)
        mids = sorted([p for p in squad if p.position == 'MID'], key=lambda p: p.projected_sum, reverse=True)
        fwds = sorted([p for p in squad if p.position == 'FWD'], key=lambda p: p.projected_sum, reverse=True)
        
        # Start with minimum: 1 GK, 3 DEF, 2 MID, 1 FWD = 7
        xi = gks[:1] + defs[:3] + mids[:2] + fwds[:1]
        
        # Fill remaining 4 slots from best remaining
        used = set(id(p) for p in xi)
        remaining = sorted(
            [p for p in squad if id(p) not in used and p.position != 'GK'],
            key=lambda p: p.projected_sum, reverse=True
        )
        xi.extend(remaining[:4])
        
        # Bench = everyone not in XI
        xi_ids = set(id(p) for p in xi)
        bench_gk = [p for p in squad if id(p) not in xi_ids and p.position == 'GK']
        bench_outfield = sorted(
            [p for p in squad if id(p) not in xi_ids and p.position != 'GK'],
            key=lambda p: p.projected_sum, reverse=True
        )
        bench = bench_gk + bench_outfield
        
        return xi, bench
    
    def close(self):
        self.conn.close()


class ChipEngine:
    """Predict chip timing based on DGW/BGW calendar and squad state."""
    
    def recommend_chip(self, gw: int, squad: List[Player], ft_available: int,
                       dgw_gws: List[int], bgw_gws: List[int],
                       chips_remaining: List[str]) -> Optional[str]:
        """
        Recommend whether to play a chip this GW.
        
        Rules (from observation):
        - WC: Play before a DGW cluster to rebuild squad for BB/TC
        - BB: Play ON a DGW (maximize 15-man scoring with doublers)
        - TC: Play ON a DGW (maximize captain with double fixtures)
        - FH: Play ON a BGW (fill squad with players who have fixtures)
        """
        if not chips_remaining:
            return None
        
        # Is this a DGW?
        is_dgw = gw in dgw_gws
        # Is this a BGW?
        is_bgw = gw in bgw_gws
        # Is next GW a DGW?
        next_is_dgw = (gw + 1) in dgw_gws
        
        # FH logic: play on BGW
        if is_bgw and 'FH' in chips_remaining:
            return 'FH'
        
        # WC logic: play 1-2 GWs before a DGW (to set up for BB)
        if next_is_dgw and 'WC' in chips_remaining and 'BB' in chips_remaining:
            return 'WC'
        
        # BB logic: play on DGW
        if is_dgw and 'BB' in chips_remaining and 'WC' not in chips_remaining:
            # Only BB if WC was just used (squad is optimal for DGW)
            return 'BB'
        
        # TC logic: play on DGW (if BB already used or saved for later)
        if is_dgw and 'TC' in chips_remaining and 'BB' not in chips_remaining:
            return 'TC'
        
        return None


if __name__ == "__main__":
    print("FPL Engine v1 loaded.")
    print("Components: TransferEngine, CaptainEngine, LineupEngine, ChipEngine")
    print("\nTo backtest, run engine_backtest.py")
