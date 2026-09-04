#!/usr/bin/env python3
"""
FPL Transfer Optimizer — self-contained desktop app.

What it does
------------
1. Connects to the FPL API to pull your current squad, prices, and transfer
   history (to compute how many free transfers you have, chips-aware).
2. Connects to Supabase to pull the definitive expected points (Transfer
   Algorithm, via the final_projections view) for the coming gameweeks.
3. Brute-forces every sensible transfer permutation (0, 1, 2 or 3 moves),
   respecting budget, the 3-players-per-club rule and squad structure.
4. Scores each candidate squad by its time-weighted expected points over the
   horizon — picking the OPTIMAL valid XI every gameweek (i.e. it accounts for
   rotation), with a configurable decay (default 0.85 per GW).
5. Ranks the moves by points gained (and shows the net after the 4-pt hit for
   any transfers beyond your free ones).

Run it
------
    python3 optimizer_app.py

Supabase credentials are read from the environment (SUPABASE_URL,
SUPABASE_SERVICE_KEY) or a local .env file; if missing, the app asks for them
in the GUI and remembers them for the session.

Only depends on `requests` (pip install requests). Tkinter ships with Python.
"""

import os
import json
import threading
import itertools
from dataclasses import dataclass, field

try:
    import requests
except ImportError:
    raise SystemExit("This app needs the 'requests' package.\n  pip install requests")

# Tkinter is only needed for the GUI. Import lazily so the engine can be
# imported/tested (and a CLI fallback used) even where Tk isn't available.
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False

# ----------------------------------------------------------------------------
# Config / constants
# ----------------------------------------------------------------------------
FPL_BASE = "https://fantasy.premierleague.com/api"
DECAY_DEFAULT = 0.85
SEASON = "2026-27"
HORIZON_DEFAULT = 8
HIT_COST = 4

POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
# Valid XI: exactly 1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD, total 11
FORMATION_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
FORMATION_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
SQUAD_STRUCTURE = {1: 2, 2: 5, 3: 5, 4: 3}  # full 15-man squad


# ----------------------------------------------------------------------------
# Environment / .env loading
# ----------------------------------------------------------------------------
def load_env():
    """Load SUPABASE_URL / SUPABASE_SERVICE_KEY from env or a nearby .env."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_ANON_KEY", "")
    if url and key:
        return url, key
    # Search a few likely .env locations
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, ".env"),
        os.path.join(here, "..", ".env"),
        os.path.join(here, "..", "fpl-tracker", ".env"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k in ("SUPABASE_URL", "PUBLIC_SUPABASE_URL") and not url:
                        url = v
                    elif k in ("SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "PUBLIC_SUPABASE_ANON_KEY") and not key:
                        key = v
    return url, key


# ----------------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------------
@dataclass
class Player:
    element_id: int
    web_name: str
    element_type: int          # 1..4
    team_id: int
    team_short: str
    now_cost: int              # tenths
    projections: dict = field(default_factory=dict)  # gw -> xpts
    # squad-only fields
    selling_price: int = 0
    purchase_price: int = 0
    in_squad: bool = False

    def twxp(self, gws, decay):
        """Standalone time-weighted xP over the given ordered gameweeks."""
        total = 0.0
        for i, gw in enumerate(gws):
            total += self.projections.get(gw, 0.0) * (decay ** i)
        return total


# ----------------------------------------------------------------------------
# FPL API
# ----------------------------------------------------------------------------
def fpl_get(path):
    r = requests.get(f"{FPL_BASE}/{path}", headers={"User-Agent": "FPL-Optimizer/1.0"}, timeout=20)
    r.raise_for_status()
    return r.json()


def get_bootstrap():
    return fpl_get("bootstrap-static/")


def get_next_and_current_gw(bootstrap):
    nxt = cur = None
    for e in bootstrap["events"]:
        if e.get("is_next"):
            nxt = e["id"]
        if e.get("is_current"):
            cur = e["id"]
    if nxt is None:
        # fallback: first unfinished
        for e in bootstrap["events"]:
            if not e.get("finished"):
                nxt = e["id"]
                break
    if cur is None:
        cur = (nxt or 2) - 1
    return cur, (nxt or (cur + 1))


def selling_price(purchase, current):
    """FPL selling price: you get purchase + floor(half the rise)."""
    if current <= purchase:
        return current
    return purchase + (current - purchase) // 2


def compute_free_transfers(manager_id, next_gw, chips):
    """Free transfers available going into next_gw.

    There is no transfer at GW1 — you just pick your initial squad, so FTs only
    begin to exist from GW2. You get 1 free transfer at GW2, and each subsequent
    gameweek you bank +1 (capped at 5), minus any transfers already made that GW.
    Wildcard/Free-Hit gameweeks don't consume banked FTs.
    """
    if next_gw <= 1:
        return 0
    transfers = fpl_get(f"entry/{manager_id}/transfers/")
    by_gw = {}
    for t in transfers:
        by_gw[t["event"]] = by_gw.get(t["event"], 0) + 1
    chip_gw = {c["event"]: c["name"] for c in chips if c.get("name") in ("wildcard", "freehit")}

    ft = 1  # GW2 is the first gameweek a free transfer exists
    # simulate completed gameweeks GW2 .. next_gw-1
    for gw in range(3, next_gw):
        ft = min(5, ft + 1)
        if gw in chip_gw:
            continue
        ft = max(0, ft - by_gw.get(gw, 0))
    # account for any transfers already made in GW2 (the seed week)
    if 2 < next_gw:
        ft = max(0, ft - by_gw.get(2, 0)) if 2 not in chip_gw else ft
    # bank the +1 for next_gw itself (unless next_gw is GW2, already seeded)
    if next_gw > 2:
        ft = min(5, ft + 1)
    return ft


def get_manager_squad(manager_id, bootstrap):
    """Return (manager_info, list_of_element_ids_with_purchase_prices, bank, free_transfers)."""
    entry = fpl_get(f"entry/{manager_id}/")
    cur_gw, next_gw = get_next_and_current_gw(bootstrap)

    # picks for the latest available GW
    picks = None
    for gw in (cur_gw, cur_gw - 1, next_gw - 1):
        if gw and gw >= 1:
            try:
                picks = fpl_get(f"entry/{manager_id}/event/{gw}/picks/")
                break
            except Exception:
                continue
    if picks is None:
        raise RuntimeError("Could not fetch your squad picks yet for this season.")

    transfers = fpl_get(f"entry/{manager_id}/transfers/")
    try:
        history = fpl_get(f"entry/{manager_id}/history/")
        chips = history.get("chips", [])
    except Exception:
        chips = []

    # current prices from bootstrap
    price_by_id = {e["id"]: e for e in bootstrap["elements"]}

    # purchase price: from transfers if bought, else start-of-season price
    purchase = {}
    for t in transfers:
        purchase[t["element_in"]] = t["element_in_cost"]

    squad_ids = []
    purchase_prices = {}
    for pick in picks["picks"]:
        eid = pick["element"]
        squad_ids.append(eid)
        el = price_by_id.get(eid, {})
        now = el.get("now_cost", 0)
        if eid in purchase:
            pp = purchase[eid]
        else:
            pp = now - el.get("cost_change_start", 0)
        purchase_prices[eid] = pp

    bank = picks["entry_history"]["bank"]
    ft = compute_free_transfers(manager_id, next_gw, chips)

    manager = {
        "name": f"{entry.get('player_first_name','')} {entry.get('player_last_name','')}".strip(),
        "team_name": entry.get("name", ""),
        "overall_rank": entry.get("summary_overall_rank"),
        "gw": picks["entry_history"]["event"],
    }
    return manager, squad_ids, purchase_prices, bank, ft, next_gw


# ----------------------------------------------------------------------------
# Supabase — projections + player universe
# ----------------------------------------------------------------------------
def supabase_get(url, key, path):
    r = requests.get(f"{url}/rest/v1/{path}",
                     headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def load_players(url, key, bootstrap, next_gw, horizon):
    """Build the full player universe with projections for next_gw..next_gw+horizon-1."""
    gws = list(range(next_gw, min(next_gw + horizon, 39)))

    # team lookup
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    # base players from bootstrap (names, position, team, price)
    players = {}
    for e in bootstrap["elements"]:
        players[e["id"]] = Player(
            element_id=e["id"],
            web_name=e["web_name"],
            element_type=e["element_type"],
            team_id=e["team"],
            team_short=team_short.get(e["team"], "?"),
            now_cost=e["now_cost"],
        )

    # latest uploaded_for_gw
    latest = supabase_get(url, key,
        "final_projections?select=uploaded_for_gw&order=uploaded_for_gw.desc&limit=1")
    latest_gw = latest[0]["uploaded_for_gw"] if latest else next_gw

    # projections, batched
    ids = list(players.keys())
    BATCH = 80
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        idlist = ",".join(str(x) for x in chunk)
        rows = supabase_get(url, key,
            f"final_projections?select=element_id,gameweek,expected_points"
            f"&element_id=in.({idlist})&uploaded_for_gw=eq.{latest_gw}"
            f"&gameweek=gte.{gws[0]}&gameweek=lte.{gws[-1]}&limit=2000")
        for r in rows:
            p = players.get(r["element_id"])
            if p and r["expected_points"] is not None:
                p.projections[r["gameweek"]] = float(r["expected_points"])

    return players, gws, latest_gw


# ----------------------------------------------------------------------------
# Scoring engine — optimal valid XI + captain per GW, decayed over the horizon
# ----------------------------------------------------------------------------
# All valid outfield formations (DEF, MID, FWD) with exactly 10 outfield + 1 GK.
VALID_FORMATIONS = [
    (d, m, f)
    for d in range(3, 6)      # 3-5 DEF
    for m in range(2, 6)      # 2-5 MID
    for f in range(1, 4)      # 1-3 FWD
    if d + m + f == 10
]


def best_xi_detail(squad_players, gw):
    """Like best_xi_points but returns the full breakdown for one gameweek:
    (total, formation, starters, captain, bench).
    starters/bench are Player objects; captain is the doubled Player.
    """
    playing = [(p, p.projections.get(gw, 0.0)) for p in squad_players]
    by_pos = {1: [], 2: [], 3: [], 4: []}
    for p, v in playing:
        by_pos[p.element_type].append((p, v))
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x[1], reverse=True)

    if not by_pos[1]:
        return 0.0, None, [], None, []

    gk = by_pos[1][0]
    best = None
    for d, m, f in VALID_FORMATIONS:
        if len(by_pos[2]) < d or len(by_pos[3]) < m or len(by_pos[4]) < f:
            continue
        starters = [gk] + by_pos[2][:d] + by_pos[3][:m] + by_pos[4][:f]
        total = sum(v for _, v in starters)
        cap = max(starters, key=lambda x: x[1])
        total += cap[1]  # captain doubled
        if best is None or total > best[0]:
            best = (total, (d, m, f), starters, cap)

    if best is None:
        return 0.0, None, [], None, []

    total, formation, starters, cap = best
    starter_ids = {p.element_id for p, _ in starters}
    bench = [(p, v) for p, v in playing if p.element_id not in starter_ids]
    bench.sort(key=lambda x: x[1], reverse=True)
    return total, formation, starters, cap, bench


def best_xi_points(squad_players, gw):
    """Best legal XI points for one gameweek, INCLUDING captaincy."""
    return best_xi_detail(squad_players, gw)[0]


def squad_twxp(squad_players, gws, decay):
    """Time-weighted expected points across the horizon, optimal XI each GW."""
    total = 0.0
    for i, gw in enumerate(gws):
        total += best_xi_points(squad_players, gw) * (decay ** i)
    return total


# ----------------------------------------------------------------------------
# Brute-force transfer search
# ----------------------------------------------------------------------------
@dataclass
class Move:
    transfers: list          # list of (out_player, in_player)
    twxp: float
    delta: float             # twxp gain vs current squad
    hit: int                 # points hit (0 if within free transfers)
    net: float               # delta - hit
    squad_ids: set


def club_counts(players):
    c = {}
    for p in players:
        c[p.team_id] = c.get(p.team_id, 0) + 1
    return c


def valid_squad(players):
    """15 players, correct positional structure, max 3 per club."""
    if len(players) != 15:
        return False
    pos = {1: 0, 2: 0, 3: 0, 4: 0}
    for p in players:
        pos[p.element_type] += 1
    if pos != SQUAD_STRUCTURE:
        return False
    cc = club_counts(players)
    return all(v <= 3 for v in cc.values())


def optimize(current_ids, players, gws, decay, bank, free_transfers,
             max_transfers=2, top_n=20, progress_cb=None):
    """Exhaustive brute force of transfer plans up to max_transfers deep.

    For every combination of squad players sold and same-position replacements
    bought (from the FULL candidate pool), the resulting 15-man squad is scored
    by its decayed time-weighted expected points, where EACH gameweek
    independently uses the optimal valid XI + captain. Respects budget, the
    3-per-club rule and exact squad structure.
    """
    current = [players[i] for i in current_ids]
    base_twxp = squad_twxp(current, gws, decay)

    # Full candidate pool by position (everyone not owned who has projections)
    owned = set(current_ids)
    by_pos_candidates = {1: [], 2: [], 3: [], 4: []}
    for p in players.values():
        if p.element_id in owned or not p.projections:
            continue
        by_pos_candidates[p.element_type].append(p)
    # Sort by horizon value (helps early-exit / readability; NOT truncated)
    for pos in by_pos_candidates:
        by_pos_candidates[pos].sort(key=lambda p: p.twxp(gws, decay), reverse=True)

    results = []
    results.append(Move([], base_twxp, 0.0, 0, 0.0, set(current_ids)))

    sell = {p.element_id: p.selling_price for p in current}

    def try_plans(depth):
        combos = list(itertools.combinations(current, depth))
        total_combos = len(combos)
        for ci, out_combo in enumerate(combos):
            if progress_cb and ci % 20 == 0:
                progress_cb(f"{depth}-transfer search: {ci}/{total_combos} out-combos…")
            need_positions = [p.element_type for p in out_combo]
            budget = bank + sum(sell[p.element_id] for p in out_combo)
            out_ids = {p.element_id for p in out_combo}
            kept = [p for p in current if p.element_id not in out_ids]
            kept_club = club_counts(kept)

            cand_lists = [by_pos_candidates[pos] for pos in need_positions]

            for in_combo in itertools.product(*cand_lists):
                in_ids = {p.element_id for p in in_combo}
                if len(in_ids) != depth:
                    continue
                if sum(p.now_cost for p in in_combo) > budget:
                    continue
                # 3-per-club
                cc = dict(kept_club)
                ok = True
                for p in in_combo:
                    cc[p.team_id] = cc.get(p.team_id, 0) + 1
                    if cc[p.team_id] > 3:
                        ok = False
                        break
                if not ok:
                    continue
                new_players = kept + list(in_combo)
                tw = squad_twxp(new_players, gws, decay)
                delta = tw - base_twxp
                hit = max(0, depth - free_transfers) * HIT_COST
                results.append(Move(
                    transfers=list(zip(out_combo, in_combo)),
                    twxp=tw, delta=delta, hit=hit, net=delta - hit,
                    squad_ids={p.element_id for p in new_players},
                ))

    for depth in range(1, max_transfers + 1):
        if progress_cb:
            progress_cb(f"Searching {depth}-transfer plans (exhaustive)…")
        try_plans(depth)

    # dedupe: collapse plans with the same INCOMING players + same net gain
    best = {}
    for m in results:
        in_ids = frozenset(n.element_id for _, n in m.transfers)
        key = (in_ids, round(m.net, 2))
        if key not in best or m.net > best[key].net:
            best[key] = m
    ranked = sorted(best.values(), key=lambda m: (m.net, m.delta), reverse=True)
    return base_twxp, ranked[:top_n]


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
_TkBase = tk.Tk if TK_AVAILABLE else object


class OptimizerApp(_TkBase):
    def __init__(self):
        super().__init__()
        self.title("FPL Transfer Optimizer")
        self.geometry("1080x760")
        self.configure(bg="#0f1117")

        self.sb_url, self.sb_key = load_env()
        self.bootstrap = None
        self.players = None
        self.gws = None
        self.current_ids = None
        self.bank = 0
        self.free_transfers = 1
        self.next_gw = None
        self.manager = None

        self._build_ui()

    # ---- UI construction ----
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background="#0f1117")
        style.configure("TLabel", background="#0f1117", foreground="#e6e8ec", font=("Helvetica", 11))
        style.configure("Head.TLabel", font=("Helvetica", 15, "bold"), foreground="#ffffff")
        style.configure("Sub.TLabel", foreground="#9aa0aa", font=("Helvetica", 10))
        style.configure("TButton", font=("Helvetica", 11, "bold"))
        style.configure("TEntry", fieldbackground="#1a1d27", foreground="#e6e8ec")

        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", padx=16, pady=(14, 6))

        ttk.Label(top, text="FPL Transfer Optimizer", style="Head.TLabel").grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(top, text="Brute-forces the highest expected-points transfer moves over the coming gameweeks (0.85 decay, optimal XI each week).",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=6, sticky="w", pady=(0, 8))

        # Manager ID
        ttk.Label(top, text="Manager ID:").grid(row=2, column=0, sticky="e", **pad)
        self.manager_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.manager_var, width=14).grid(row=2, column=1, sticky="w", **pad)
        self.load_btn = ttk.Button(top, text="Load squad", command=self.on_load)
        self.load_btn.grid(row=2, column=2, sticky="w", **pad)

        # Free transfers override
        ttk.Label(top, text="Free transfers:").grid(row=2, column=3, sticky="e", **pad)
        self.ft_var = tk.StringVar(value="auto")
        self.ft_combo = ttk.Combobox(top, textvariable=self.ft_var, width=6,
                                     values=["auto", "0", "1", "2", "3", "4", "5"], state="readonly")
        self.ft_combo.grid(row=2, column=4, sticky="w", **pad)

        # Options row
        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=16, pady=(0, 6))
        ttk.Label(opts, text="Max transfers to consider:").grid(row=0, column=0, sticky="e", **pad)
        self.depth_var = tk.StringVar(value="2")
        ttk.Combobox(opts, textvariable=self.depth_var, width=4, values=["1", "2", "3"], state="readonly").grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(opts, text="Horizon (GWs):").grid(row=0, column=2, sticky="e", **pad)
        self.horizon_var = tk.StringVar(value=str(HORIZON_DEFAULT))
        ttk.Combobox(opts, textvariable=self.horizon_var, width=4, values=["3", "4", "5", "6", "8"], state="readonly").grid(row=0, column=3, sticky="w", **pad)

        ttk.Label(opts, text="Decay:").grid(row=0, column=4, sticky="e", **pad)
        self.decay_var = tk.StringVar(value=str(DECAY_DEFAULT))
        ttk.Entry(opts, textvariable=self.decay_var, width=6).grid(row=0, column=5, sticky="w", **pad)

        self.opt_btn = ttk.Button(opts, text="Optimize ▶", command=self.on_optimize, state="disabled")
        self.opt_btn.grid(row=0, column=6, sticky="w", padx=16)

        # Status line
        self.status_var = tk.StringVar(value="Enter your Manager ID and click ‘Load squad’.")
        ttk.Label(self, textvariable=self.status_var, style="Sub.TLabel").pack(fill="x", padx=16, pady=(0, 6))

        # Squad summary
        self.summary_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.summary_var).pack(fill="x", padx=16, pady=(0, 6))

        # Results
        cols = ("rank", "move", "gain", "hit", "net")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=16)
        headers = {"rank": ("#", 40), "move": ("Move", 620), "gain": ("Δ pts (8wk)", 110),
                   "hit": ("Hit", 60), "net": ("Net", 90)}
        for c in cols:
            label, w = headers[c]
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor=("w" if c == "move" else "center"))
        self.tree.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self.on_select_move)

        # Detail box
        self.detail = tk.Text(self, height=8, bg="#1a1d27", fg="#e6e8ec",
                              font=("Menlo", 10), wrap="word", relief="flat")
        self.detail.pack(fill="x", padx=16, pady=(0, 14))
        self.detail.insert("1.0", "Select a suggestion above to see the resulting squad and per-gameweek breakdown.")
        self.detail.configure(state="disabled")

        self._ranked = []

    # ---- actions ----
    def set_status(self, msg):
        self.status_var.set(msg)
        self.update_idletasks()

    def on_load(self):
        mid = self.manager_var.get().strip()
        if not mid.isdigit():
            messagebox.showerror("Manager ID", "Enter your numeric FPL Manager ID.")
            return
        if not (self.sb_url and self.sb_key):
            self._prompt_supabase()
            if not (self.sb_url and self.sb_key):
                return
        self.load_btn.configure(state="disabled")
        threading.Thread(target=self._load_worker, args=(int(mid),), daemon=True).start()

    def _load_worker(self, mid):
        try:
            self.set_status("Fetching FPL data…")
            self.bootstrap = get_bootstrap()
            manager, squad_ids, purchase, bank, ft, next_gw = get_manager_squad(mid, self.bootstrap)
            horizon = int(self.horizon_var.get())
            self.set_status("Loading projections from Supabase…")
            players, gws, latest_gw = load_players(self.sb_url, self.sb_key, self.bootstrap, next_gw, horizon)

            # attach squad + prices
            price_by_id = {e["id"]: e for e in self.bootstrap["elements"]}
            for eid in squad_ids:
                p = players.get(eid)
                if not p:
                    continue
                p.in_squad = True
                p.purchase_price = purchase.get(eid, p.now_cost)
                p.selling_price = selling_price(p.purchase_price, p.now_cost)

            self.players = players
            self.gws = gws
            self.current_ids = squad_ids
            self.bank = bank
            self.free_transfers = ft
            self.next_gw = next_gw
            self.manager = manager

            covered = sum(1 for eid in squad_ids if players.get(eid) and players[eid].projections)
            self.after(0, lambda: self._on_loaded(covered, latest_gw))
        except Exception as e:
            self.after(0, lambda: self._load_error(str(e)))

    def _on_loaded(self, covered, latest_gw):
        m = self.manager
        base = squad_twxp([self.players[i] for i in self.current_ids], self.gws, self._decay())
        self.summary_var.set(
            f"{m['team_name']}  ·  {m['name']}  ·  Bank £{self.bank/10:.1f}m  ·  "
            f"Free transfers: {self.free_transfers}  ·  Planning GW{self.next_gw}  ·  "
            f"Projection set: GW{latest_gw}  ·  Current squad 8wk TWxP: {base:.1f}"
        )
        self.set_status(f"Loaded. {covered}/15 squad players have projections. Set options and click Optimize.")
        self.opt_btn.configure(state="normal")
        self.load_btn.configure(state="normal")

    def _load_error(self, msg):
        self.load_btn.configure(state="normal")
        self.set_status("Load failed.")
        messagebox.showerror("Load failed", msg)

    def _decay(self):
        try:
            d = float(self.decay_var.get())
            return d if 0 < d <= 1 else DECAY_DEFAULT
        except ValueError:
            return DECAY_DEFAULT

    def _effective_ft(self):
        if self.ft_var.get() == "auto":
            return self.free_transfers
        try:
            return int(self.ft_var.get())
        except ValueError:
            return self.free_transfers

    def on_optimize(self):
        if not self.current_ids:
            return
        self.opt_btn.configure(state="disabled")
        threading.Thread(target=self._optimize_worker, daemon=True).start()

    def _optimize_worker(self):
        try:
            depth = int(self.depth_var.get())
            decay = self._decay()
            ft = self._effective_ft()
            # rebuild horizon if changed
            horizon = int(self.horizon_var.get())
            self.gws = list(range(self.next_gw, min(self.next_gw + horizon, 39)))
            self.set_status(f"Brute-forcing up to {depth} transfer(s) over {len(self.gws)} GWs…")
            base, ranked = optimize(
                self.current_ids, self.players, self.gws, decay,
                self.bank, ft, max_transfers=depth, top_n=25,
                progress_cb=lambda s: self.set_status(s),
            )
            self._ranked = ranked
            self.after(0, lambda: self._show_results(base, ranked))
        except Exception as e:
            self.after(0, lambda: self._opt_error(str(e)))

    def _show_results(self, base, ranked):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, m in enumerate(ranked):
            if not m.transfers:
                move_txt = "No transfer (keep current squad)"
            else:
                parts = [f"{o.web_name} → {n.web_name}" for o, n in m.transfers]
                move_txt = "  +  ".join(parts)
            self.tree.insert("", "end", iid=str(i), values=(
                i, move_txt, f"{m.delta:+.1f}", f"-{m.hit}" if m.hit else "0", f"{m.net:+.1f}"
            ))
        self.set_status(f"Done. Current 8wk TWxP {base:.1f}. Showing top {len(ranked)} plans by net gain.")
        self.opt_btn.configure(state="normal")
        if ranked:
            self.tree.selection_set("0")
            self.on_select_move(None)

    def _opt_error(self, msg):
        self.opt_btn.configure(state="normal")
        self.set_status("Optimize failed.")
        messagebox.showerror("Optimize failed", msg)

    def on_select_move(self, _evt):
        sel = self.tree.selection()
        if not sel or not self._ranked:
            return
        idx = int(sel[0])
        if idx >= len(self._ranked):
            return
        m = self._ranked[idx]
        squad = [self.players[i] for i in m.squad_ids]
        decay = self._decay()

        lines = []
        if m.transfers:
            lines.append("TRANSFERS:")
            for o, n in m.transfers:
                lines.append(f"   OUT  {o.web_name:16s} (£{o.selling_price/10:.1f}m)   "
                             f"IN  {n.web_name:16s} (£{n.now_cost/10:.1f}m)")
            lines.append(f"   Hit: -{m.hit} pts   ·   Δ over horizon: {m.delta:+.1f}   ·   Net: {m.net:+.1f}")
        else:
            lines.append("No transfer — keep the current squad.")
        lines.append("")

        # per-GW optimal XI points
        lines.append("Projected best-XI points by gameweek:")
        row = "   "
        for gw in self.gws:
            row += f"GW{gw}: {best_xi_points(squad, gw):5.1f}   "
        lines.append(row)

        # resulting squad by position
        lines.append("")
        lines.append("RESULTING SQUAD:")
        for pos in (1, 2, 3, 4):
            names = [f"{p.web_name}({p.team_short})" for p in squad if p.element_type == pos]
            lines.append(f"   {POS_NAMES[pos]}: " + ", ".join(names))

        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", "\n".join(lines))
        self.detail.configure(state="disabled")

    def _prompt_supabase(self):
        win = tk.Toplevel(self)
        win.title("Supabase connection")
        win.configure(bg="#0f1117")
        ttk.Label(win, text="Supabase URL:").grid(row=0, column=0, padx=8, pady=8, sticky="e")
        url_v = tk.StringVar(value=self.sb_url)
        ttk.Entry(win, textvariable=url_v, width=48).grid(row=0, column=1, padx=8, pady=8)
        ttk.Label(win, text="Service/anon key:").grid(row=1, column=0, padx=8, pady=8, sticky="e")
        key_v = tk.StringVar(value=self.sb_key)
        ttk.Entry(win, textvariable=key_v, width=48, show="•").grid(row=1, column=1, padx=8, pady=8)

        def save():
            self.sb_url = url_v.get().strip()
            self.sb_key = key_v.get().strip()
            win.destroy()
        ttk.Button(win, text="Save", command=save).grid(row=2, column=1, sticky="e", padx=8, pady=8)
        win.transient(self)
        win.grab_set()
        self.wait_window(win)


def run_cli():
    """Headless fallback: same optimization, printed to the terminal.
    Used when Tkinter isn't available, or via `python optimizer_app.py --cli <id>`."""
    import argparse
    ap = argparse.ArgumentParser(description="FPL Transfer Optimizer (CLI)")
    ap.add_argument("manager_id", type=int, help="Your FPL manager ID")
    ap.add_argument("--transfers", type=int, default=2, help="Max transfers to consider (1-3)")
    ap.add_argument("--horizon", type=int, default=HORIZON_DEFAULT, help="Gameweeks to look ahead")
    ap.add_argument("--decay", type=float, default=DECAY_DEFAULT)
    ap.add_argument("--free", type=int, default=None, help="Override free transfers (default: auto)")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    url, key = load_env()
    if not (url and key):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (env or .env).")

    print("Fetching FPL data…")
    bootstrap = get_bootstrap()
    manager, squad_ids, purchase, bank, ft, next_gw = get_manager_squad(args.manager_id, bootstrap)
    print("Loading projections…")
    players, gws, latest_gw = load_players(url, key, bootstrap, next_gw, args.horizon)

    for eid in squad_ids:
        p = players.get(eid)
        if p:
            p.in_squad = True
            p.purchase_price = purchase.get(eid, p.now_cost)
            p.selling_price = selling_price(p.purchase_price, p.now_cost)

    free = args.free if args.free is not None else ft
    print(f"\n{manager['team_name']} · {manager['name']}")
    print(f"Bank £{bank/10:.1f}m · Free transfers {free} · Planning GW{next_gw} · Projections GW{latest_gw}\n")

    base, ranked = optimize(squad_ids, players, gws, args.decay, bank, free,
                            max_transfers=args.transfers, top_n=args.top,
                            progress_cb=lambda s: print(s))
    print(f"\nCurrent squad {len(gws)}wk TWxP: {base:.1f}\n")
    print(f"{'#':>2}  {'NET':>6}  {'Δ':>6}  {'HIT':>4}  MOVE")
    for i, m in enumerate(ranked):
        mv = "KEEP current squad" if not m.transfers else \
            "  +  ".join(f"{o.web_name}→{n.web_name}" for o, n in m.transfers)
        print(f"{i:>2}  {m.net:>+6.1f}  {m.delta:>+6.1f}  {('-'+str(m.hit)) if m.hit else '0':>4}  {mv}")


if __name__ == "__main__":
    import sys
    if "--cli" in sys.argv:
        sys.argv.remove("--cli")
        run_cli()
    elif not TK_AVAILABLE:
        print("Tkinter isn't available in this Python build, so the GUI can't open.")
        print("Falling back to the command-line version.\n")
        print("Tip: install a Python with Tk (e.g. `brew install python-tk`) for the GUI.\n")
        run_cli()
    else:
        OptimizerApp().mainloop()
