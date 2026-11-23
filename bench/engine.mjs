// JavaScript port of the exhaustive transfer search in optimizer_app.py
// (best_xi_detail, squad_twxp, optimize with use_prune=False).
//
// Written to match the Python line for line, including the order in which
// numbers are added and how ties are broken, so results can be compared
// exactly. No algorithmic shortcuts: same work, different language.

export const HIT_COST = 4;

// Same order as Python's VALID_FORMATIONS comprehension: d 3-5, m 2-5, f 1-3.
export const FORMATIONS = [];
for (let d = 3; d < 6; d++)
  for (let m = 2; m < 6; m++)
    for (let f = 1; f < 4; f++)
      if (d + m + f === 10) FORMATIONS.push([d, m, f]);

/** Build engine players from a fixture. proj[i] is the value for gws[i] (0 if missing). */
export function loadFixture(fx) {
  const gwIndex = new Map(fx.gws.map((g, i) => [g, i]));
  const players = new Map();
  for (const p of fx.players) {
    const proj = new Float64Array(fx.gws.length);
    for (const [g, v] of p.proj) if (gwIndex.has(g)) proj[gwIndex.get(g)] = v;
    players.set(p.id, {
      id: p.id, name: p.name, pos: p.pos, team: p.team, cost: p.cost, sell: p.sell,
      proj, hasProj: p.proj.length > 0,
    });
  }
  return players;
}

/** Player.twxp: decayed sum of a player's own projections. */
function playerTwxp(p, weights) {
  let total = 0.0;
  for (let i = 0; i < weights.length; i++) total += p.proj[i] * weights[i];
  return total;
}

/** best_xi_points: best legal XI for one gameweek, captain doubled. */
export function bestXiPoints(squad, gi) {
  const byPos = [null, [], [], [], []];
  for (const p of squad) byPos[p.pos].push({ p, v: p.proj[gi] });
  for (let pos = 1; pos <= 4; pos++) byPos[pos].sort((a, b) => b.v - a.v); // stable, like Python
  if (byPos[1].length === 0) return 0.0;

  const gk = byPos[1][0];
  let best = null;
  for (const [d, m, f] of FORMATIONS) {
    if (byPos[2].length < d || byPos[3].length < m || byPos[4].length < f) continue;
    const starters = [gk, ...byPos[2].slice(0, d), ...byPos[3].slice(0, m), ...byPos[4].slice(0, f)];
    let total = 0;
    for (const s of starters) total += s.v;
    let cap = starters[0];
    for (const s of starters) if (s.v > cap.v) cap = s;  // first maximum, like max()
    total += cap.v;
    if (best === null || total > best) best = total;
  }
  return best === null ? 0.0 : best;
}

/** squad_twxp: decayed points over the horizon, best XI each week. */
export function squadTwxp(squad, weights) {
  let total = 0.0;
  for (let i = 0; i < weights.length; i++) total += bestXiPoints(squad, i) * weights[i];
  return total;
}

function* combinations(arr, k, start = 0, acc = []) {
  if (acc.length === k) { yield acc.slice(); return; }
  for (let i = start; i < arr.length; i++) {
    acc.push(arr[i]);
    yield* combinations(arr, k, i + 1, acc);
    acc.pop();
  }
}

function* product(lists, i = 0, acc = []) {
  if (i === lists.length) { yield acc.slice(); return; }
  for (const x of lists[i]) {
    acc.push(x);
    yield* product(lists, i + 1, acc);
    acc.pop();
  }
}

/** Python round(x, 2) for the dedupe key (differs only on exact binary ties). */
const round2 = (x) => Number(x.toFixed(2));

/**
 * optimize(..., use_prune=False). Returns { base, ranked, evaluated }.
 * ranked: best `topN` plans at each depth, sorted by (net, delta) descending.
 */
export function optimize(players, squadIds, weights, bank, freeTransfers, maxTransfers = 2, topN = 20) {
  const current = squadIds.map((id) => players.get(id));
  const base = squadTwxp(current, weights);

  const owned = new Set(squadIds);
  const cands = [null, [], [], [], []];
  for (const p of players.values()) {
    if (owned.has(p.id) || !p.hasProj) continue;
    cands[p.pos].push(p);
  }
  for (let pos = 1; pos <= 4; pos++) {
    const tw = new Map(cands[pos].map((p) => [p, playerTwxp(p, weights)]));
    cands[pos].sort((a, b) => tw.get(b) - tw.get(a));
  }

  // Dedupe as Python does: key (incoming set, round(net, 2)); keep the first
  // strictly-best. Map keeps first-insertion order, like a Python dict.
  const best = new Map();
  const record = (m) => {
    const key = m.transfers.map(([, n]) => n.id).sort((a, b) => a - b).join(",") + "|" + round2(m.net);
    const prev = best.get(key);
    if (prev === undefined || m.net > prev.net) best.set(key, m);
  };
  record({ transfers: [], twxp: base, delta: 0.0, hit: 0, net: 0.0 });

  let evaluated = 0;
  for (let depth = 1; depth <= maxTransfers; depth++) {
    const hit = Math.max(0, depth - freeTransfers) * HIT_COST;
    for (const outCombo of combinations(current, depth)) {
      const budget = bank + outCombo.reduce((s, p) => s + p.sell, 0);
      const outIds = new Set(outCombo.map((p) => p.id));
      const kept = current.filter((p) => !outIds.has(p.id));
      const keptClub = new Map();
      for (const p of kept) keptClub.set(p.team, (keptClub.get(p.team) || 0) + 1);
      const lists = outCombo.map((p) => cands[p.pos]);

      for (const inCombo of product(lists)) {
        if (new Set(inCombo.map((p) => p.id)).size !== depth) continue;
        if (inCombo.reduce((s, p) => s + p.cost, 0) > budget) continue;
        const cc = new Map(keptClub);
        let ok = true;
        for (const p of inCombo) {
          const n = (cc.get(p.team) || 0) + 1;
          cc.set(p.team, n);
          if (n > 3) { ok = false; break; }
        }
        if (!ok) continue;
        const tw = squadTwxp(kept.concat(inCombo), weights);
        evaluated++;
        const delta = tw - base;
        record({ transfers: outCombo.map((o, i) => [o, inCombo[i]]), twxp: tw, delta, hit, net: delta - hit });
      }
    }
  }

  const all = [...best.values()].sort((a, b) => (b.net - a.net) || (b.delta - a.delta));
  const perDepth = new Map();
  const ranked = [];
  for (const m of all) {
    const d = m.transfers.length;
    if ((perDepth.get(d) || 0) >= topN) continue;
    perDepth.set(d, (perDepth.get(d) || 0) + 1);
    ranked.push(m);
  }
  return { base, ranked, evaluated };
}

/** Plain, comparable form of a ranked list. */
export function summarise(ranked) {
  return ranked.map((m) => ({
    depth: m.transfers.length,
    moves: m.transfers.map(([o, n]) => [o.id, n.id]),
    delta: m.delta, net: m.net, twxp: m.twxp,
  }));
}
