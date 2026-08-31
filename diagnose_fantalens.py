#!/usr/bin/env python3
"""Diagnostic: inspect FantaLens data structure — how many gameweeks does the
/players listing expose per player, and is there a way to get more?"""
import requests, re, json

def fetch(url):
    resp = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
    scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if not scripts:
        return None
    return json.loads(scripts[0])

# Baseline
data = fetch('https://fantalens.com/players?page=1')
props = data.get('props') or {}
print(f"season gameweeks available: {props.get('gameweeks')}")
print(f"selectedGameweeks (default): {props.get('selectedGameweeks')}")
print(f"pagination: {props.get('pagination')}")
players = props.get('players') or []
if players:
    print(f"default xpts GWs: {sorted((players[0].get('xpts') or {}).keys())}")

# Try requesting specific gameweeks via query params (common patterns)
for qs in ['gameweeks=3,4,5', 'gameweeks[]=3&gameweeks[]=4', 'gw=4', 'gameweek=4', 'selectedGameweeks=3,4,5']:
    try:
        d = fetch(f'https://fantalens.com/players?page=1&{qs}')
        p = (d.get('props') or {}).get('players') or []
        if p:
            gws = sorted((p[0].get('xpts') or {}).keys())
            sel = (d.get('props') or {}).get('selectedGameweeks')
            print(f"  ?{qs:40s} -> xpts GWs={gws} selectedGameweeks={sel}")
    except Exception as e:
        print(f"  ?{qs:40s} -> error {e}")

