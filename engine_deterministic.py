#!/usr/bin/env python3
"""
Deterministic FPL Decision Engine.

Takes as input:
- Transfer Algorithm CSV (BCV + projections for all players)
- Current squad state (15 players with purchase prices)
- Context (FT available, ITB, chips remaining, GW number, DGW/BGW flags)

Outputs:
- Transfers to make (or hold)
- Captain + Vice Captain
- Chip to play (or none)
- Bench order

Decision logic reverse-engineered from 111 GWs of the Transfer Algorithm creator's
reasoning (see decision_rules.md for full documentation).
"""
import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path


@dataclass
class Player:
    id: int
    web_name: str
    team: str
    position: str  # GK/DEF/MID/FWD
    price: float
    bcv: float = 0.0
    gw_projection: float = 0.0
    sum_projection: float = 0.0
    is_injured: bool = False
    is_suspended: bool = False
    minutes_expected: float = 90.0


@dataclass
class SquadState:
    players: List[Player]  # 15 players
    budget_remaining: float = 0.0  # ITB
    free_transfers: int = 1
    chips_available: List[str] = field(default_factory=list)
    chips_used: List[str] = field(default_factory=list)


@dataclass
class GWContext:
    gameweek: int
    season: str
    is_dgw: bool = False  # Double GW (some teams play twice)
    is_bgw: bool = False  # Blank GW (some teams don't play)
    dgw_teams: List[str] = field(default_factory=list)
    bgw_teams: List[str] = field(default_factory=list)
    intl_break_next: bool = False  # International break after this GW
    wildcard_planned_gw: Optional[int] = None  # If WC is planned for a specific GW
    ft_cap: int = 5  # Max banked FTs (season-dependent)


@dataclass
class Decision:
    transfers_in: List[Player] = field(default_factory=list)
    transfers_out: List[Player] = field(default_factory=list)
    captain: Optional[Player] = None
    vice_captain: Optional[Player] = None
    chip_to_play: Optional[str] = None
    reasoning: str = ""


# === CORE PARAMETERS (extracted from creator's historical decisions) ===
FT_VALUE_BCV = 0.12          # Value of one free transfer in BCV terms
DEAD_MONEY_COST = 0.016      # BCV cost per £1m sitting in the bank
MIN_BCV_GAIN_ELECTIVE = 0.15 # Threshold for elective transfers
MIN_BCV_GAIN_FORCED = 0.06   # Threshold when FT near cap or pressure
INJURY_FORCES_TRANSFER = True # Injured players in XI force a transfer
MAX_HITS_PER_GW = 0          # The creator almost never takes hits

# Decision tree (from 2024-25 analysis):
# IF FT == 1: ALWAYS transfer (can't bank)
# IF injured starter: TRANSFER (replace the injured player)
# IF best BCV gain >= 0.15: TRANSFER (sell lowest BCV, buy highest available)
# IF BCV gain >= 0.06 AND FT >= 4: TRANSFER (use-it-or-lose-it)
# ELSE: HOLD
#
# WHO TO SELL: lowest BCV in starting XI (or injured player if present)
# WHO TO BUY: highest BCV at same position, within budget


def evaluate_transfer(player_out: Player, player_in: Player, 
                      ft_cost: int, itb_change: float) -> float:
    """
    Calculate net BCV gain of a transfer.
    
    Returns: net BCV gain (positive = worth making)
    """
    bcv_gain = player_in.bcv - player_out.bcv
    ft_penalty = ft_cost * FT_VALUE_BCV
    
    # Dead money adjustment
    dead_money_penalty = max(0, itb_change) * DEAD_MONEY_COST
    
    net_gain = bcv_gain - ft_penalty - dead_money_penalty
    return net_gain


def find_best_transfers(squad: SquadState, all_players: List[Player], 
                        context: GWContext) -> List[Tuple[Player, Player, float]]:
    """
    Find the best possible transfers ranked by net BCV gain.
    
    Returns list of (player_out, player_in, net_gain) tuples.
    """
    owned_ids = {p.id for p in squad.players}
    candidates = []
    
    for player_out in squad.players:
        # Skip players we can't sell (only bench GK is truly unsellable)
        # Consider all players in the squad as potential sells
        
        # Find valid replacements (same position, within budget)
        max_price = player_out.price + squad.budget_remaining
        
        for player_in in all_players:
            if player_in.id in owned_ids:
                continue
            if player_in.position != player_out.position:
                continue
            if player_in.price > max_price:
                continue
            if player_in.is_injured or player_in.is_suspended:
                continue
            
            # Check max 3 per team constraint
            team_count = sum(1 for p in squad.players if p.team == player_in.team and p.id != player_out.id)
            if team_count >= 3:
                continue
            
            # Calculate net gain (1 FT cost)
            itb_change = (player_out.price - player_in.price)  # positive if downgrade
            net_gain = evaluate_transfer(player_out, player_in, ft_cost=1, 
                                        itb_change=max(0, squad.budget_remaining + itb_change))
            
            if net_gain > -0.05:  # Only consider moves that aren't terrible
                candidates.append((player_out, player_in, net_gain))
    
    # Sort by net gain descending
    candidates.sort(key=lambda x: -x[2])
    return candidates


def decide_transfers(squad: SquadState, all_players: List[Player], 
                     context: GWContext) -> List[Tuple[Player, Player]]:
    """
    Decide which transfers to make this GW.
    
    Rules:
    1. Injured XI players force a transfer (unless near WC)
    2. Make any transfer with net BCV gain > threshold
    3. Save FT if no move meets threshold
    4. Near FT cap: lower threshold (use-it-or-lose-it)
    """
    transfers = []
    available_ft = squad.free_transfers
    
    # Adjust threshold based on context
    threshold = MIN_BCV_GAIN_SINGLE
    
    # If approaching FT cap, lower threshold
    if available_ft >= context.ft_cap - 1:
        threshold *= 0.5  # More willing to spend when capped
    
    # If WC is imminent (within 2 GWs), raise threshold dramatically
    if context.wildcard_planned_gw and context.wildcard_planned_gw - context.gameweek <= 2:
        threshold = MIN_BCV_GAIN_STRONG  # Only make moves if very strong
    
    # If international break is next, save a FT
    if context.intl_break_next and available_ft <= 1:
        threshold *= 1.5  # More reluctant to spend last FT before break
    
    # Step 1: Handle injuries (forced transfers)
    injured_xi = [p for p in squad.players if p.is_injured and p.minutes_expected < 30]
    if injured_xi and INJURY_FORCES_TRANSFER:
        for injured in injured_xi:
            if available_ft <= 0:
                break
            # Find best replacement
            candidates = find_best_transfers(squad, all_players, context)
            for out, inn, gain in candidates:
                if out.id == injured.id:
                    transfers.append((out, inn))
                    available_ft -= 1
                    # Update squad state for next iteration
                    squad.players = [p if p.id != out.id else inn for p in squad.players]
                    squad.budget_remaining += out.price - inn.price
                    break
    
    # Step 2: BCV-driven transfers
    if available_ft > 0:
        candidates = find_best_transfers(squad, all_players, context)
        for out, inn, gain in candidates:
            if available_ft <= 0:
                break
            if gain >= threshold:
                # Don't sell a player we just bought
                if any(t[1].id == out.id for t in transfers):
                    continue
                transfers.append((out, inn))
                available_ft -= 1
                squad.players = [p if p.id != out.id else inn for p in squad.players]
                squad.budget_remaining += out.price - inn.price
            else:
                break  # Sorted by gain, so nothing below will be better
    
    return transfers


def decide_captain(squad: SquadState, captaincy_rankings: List[Player] = None) -> Tuple[Player, Player]:
    """
    Captain decision: follow the Captaincy Calculator rankings.
    If no external rankings, use GW projection from the algorithm.
    """
    if captaincy_rankings and len(captaincy_rankings) >= 2:
        return captaincy_rankings[0], captaincy_rankings[1]
    
    # Fallback: highest projected player in the squad
    eligible = [p for p in squad.players if p.gw_projection > 0]
    eligible.sort(key=lambda p: -p.gw_projection)
    
    if len(eligible) >= 2:
        return eligible[0], eligible[1]
    elif len(eligible) == 1:
        return eligible[0], eligible[0]
    else:
        # Last resort: highest BCV
        by_bcv = sorted(squad.players, key=lambda p: -p.bcv)
        return by_bcv[0], by_bcv[1] if len(by_bcv) > 1 else by_bcv[0]


def decide_chip(squad: SquadState, context: GWContext) -> Optional[str]:
    """
    Chip decision based on pre-planned strategy + situational triggers.
    
    The creator plans chips 6-10 GWs ahead. This function implements
    the triggers that activate a chip:
    - Wildcard: when squad has decayed significantly or pre-planned
    - Free Hit: for BGWs where squad is badly exposed
    - Bench Boost: DGW where all 15 have double fixtures
    - Triple Captain: DGW with a dominant premium (Haaland/Salah)
    """
    if not squad.chips_available:
        return None
    
    # Wildcard: pre-planned or emergency
    if "wildcard" in squad.chips_available:
        if context.wildcard_planned_gw == context.gameweek:
            return "wildcard"
        # Emergency: 3+ injuries in XI
        injured_count = sum(1 for p in squad.players if p.is_injured)
        if injured_count >= 3:
            return "wildcard"
    
    # Free Hit: BGW where 3+ squad players blank
    if "free_hit" in squad.chips_available and context.is_bgw:
        blanking = sum(1 for p in squad.players if p.team in context.bgw_teams)
        if blanking >= 3:
            return "free_hit"
    
    # Bench Boost: DGW where most of squad has doubles
    if "bench_boost" in squad.chips_available and context.is_dgw:
        doubles = sum(1 for p in squad.players if p.team in context.dgw_teams)
        if doubles >= 12:
            return "bench_boost"
    
    # Triple Captain: DGW with a dominant premium
    if "triple_captain" in squad.chips_available and context.is_dgw:
        premiums = [p for p in squad.players if p.price >= 12.0 and p.team in context.dgw_teams]
        if premiums:
            return "triple_captain"
    
    return None


def make_decision(squad: SquadState, all_players: List[Player], 
                  context: GWContext,
                  captaincy_rankings: List[Player] = None) -> Decision:
    """
    Main entry point: produce a complete GW decision.
    """
    decision = Decision()
    reasoning_parts = []
    
    # 1. Chip decision (check first as it affects transfer logic)
    chip = decide_chip(squad, context)
    if chip:
        decision.chip_to_play = chip
        reasoning_parts.append(f"Playing {chip} this GW.")
        if chip == "wildcard":
            reasoning_parts.append("Unlimited transfers available.")
            # WC logic: rebuild the entire squad from the algorithm
            # (This is complex — would need full optimiser)
        elif chip == "free_hit":
            reasoning_parts.append("One-week squad for this blank/double GW.")
    
    # 2. Transfers
    if chip != "wildcard" and chip != "free_hit":
        transfers = decide_transfers(squad, all_players, context)
        for out, inn in transfers:
            decision.transfers_in.append(inn)
            decision.transfers_out.append(out)
            reasoning_parts.append(
                f"Transfer: {out.web_name} -> {inn.web_name} "
                f"(BCV +{inn.bcv - out.bcv:.3f})"
            )
        
        if not transfers:
            reasoning_parts.append(
                f"No transfer meets threshold ({MIN_BCV_GAIN_SINGLE:.2f} BCV). "
                f"Banking FT (now {squad.free_transfers + 1})."
            )
    
    # 3. Captain
    captain, vc = decide_captain(squad, captaincy_rankings)
    decision.captain = captain
    decision.vice_captain = vc
    reasoning_parts.append(f"Captain: {captain.web_name}, VC: {vc.web_name}")
    
    decision.reasoning = " | ".join(reasoning_parts)
    return decision


if __name__ == "__main__":
    # Example usage with mock data
    print("Deterministic Decision Engine loaded.")
    print("Use make_decision(squad, all_players, context) to generate decisions.")
    print(f"\nKey parameters:")
    print(f"  FT value: {FT_VALUE_BCV} BCV")
    print(f"  Min BCV gain (single transfer): {MIN_BCV_GAIN_SINGLE}")
    print(f"  Strong BCV gain (always make): {MIN_BCV_GAIN_STRONG}")
    print(f"  Dead money cost: {DEAD_MONEY_COST} BCV per £1m ITB")
    print(f"  Injury forces transfer: {INJURY_FORCES_TRANSFER}")
