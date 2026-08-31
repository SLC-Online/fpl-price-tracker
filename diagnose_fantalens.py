#!/usr/bin/env python3
"""Diagnostic: inspect FantaLens data structure — how many gameweeks does the
/players listing expose per player, and is there a way to get more?"""
import requests, re, json

resp = requests.get('https://fantalens.com/players?page=1', timeout=20,
                    headers={'User-Agent': 'Mozilla/5.0'})
print(f"Status: {resp.status_code}")
scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
print(f"JSON scripts found: {len(scripts)}")

if scripts:
    data = json.loads(scripts[0])
    props = data.get('props') or {}
    print(f"props keys: {list(props.keys())}")
    players = props.get('players') or []
    print(f"players on page 1: {len(players)}")
    if players:
        p = players[0]
        print(f"\nfirst player keys: {list(p.keys())}")
        xp = p.get('xpts')
        print(f"xpts type: {type(xp).__name__}")
        if isinstance(xp, dict):
            print(f"xpts gameweek keys: {sorted(xp.keys())}")
            for gw, v in list(xp.items())[:1]:
                print(f"  GW{gw} structure: {json.dumps(v)[:300] if isinstance(v, dict) else v}")
        elif isinstance(xp, (int, float)):
            print(f"  xpts is a single scalar: {xp} (only one GW exposed in listing)")
        # Check for any other GW-related fields
        gw_fields = [k for k in p.keys() if 'gw' in k.lower() or 'gameweek' in k.lower() or 'fixture' in k.lower()]
        print(f"other GW/fixture fields: {gw_fields}")
