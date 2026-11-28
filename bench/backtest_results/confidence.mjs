import { readFileSync } from 'node:fs';
const rd = (f) => readFileSync(f, 'utf8').trim().split('\n').map(JSON.parse);
const follow = rd('/tmp/wc_follow_403851_d0.85.jsonl'), hold = new Map(rd('/tmp/wc_hold_403851_d0.85.jsonl').map((r) => [r.u, r]));
const ext = new Map(rd('/tmp/wc_ext_403851_d0.85.jsonl').map((r) => [`${r.u}:${r.k}`, r]));
const net = (wk) => wk.points - wk.hits;
// Week-by-week realised points of a path over GW u..u+7: each re-solved week's own week-0 line-up, then the last plan for weeks after GW12.
function realise(u, solved) {   // solved: [{w, A}] in order
	const out = [];
	for (const s of solved) out.push(net(s.A.weeks[0]));
	const last = solved[solved.length - 1];
	for (let t = 1; out.length < 8; t++) out.push(net(last.A.weeks[t]));
	return out;
}
let issues = [];
const rows = [];
for (const c of follow) {
	const h = hold.get(c.u), e = ext.get(`${c.u}:${c.k}`);
	if (!h || !e) continue;
	if (Math.abs(h.steps[0].A.objective - c.steps[0].B.objective) > 1e-6) issues.push(`GW${c.u}: hold start ${h.steps[0].A.objective} vs follow B ${c.steps[0].B.objective}`);
	const s0 = c.steps[0];
	const predicted = s0.A.weeks.reduce((s, w, t) => s + net(w) - net(s0.B.weeks[t]), 0);
	const committed = realise(c.u, [...c.steps.map((s) => ({ w: s.w, A: s.A })), ...e.steps]);
	const held = realise(c.u, h.steps);
	const realised = committed.reduce((a, b) => a + b, 0) - held.reduce((a, b) => a + b, 0);
	rows.push({ u: c.u, k: c.k, predicted, realised });
}
if (issues.length) console.log('CONSISTENCY:', issues.join('; ')); else console.log(`consistency: every by-the-book path starts exactly where commit and follow's no-wildcard plan did (${hold.size} starts)`);
console.log('\nWildcard benefit over 8 weeks (points, no decay): predicted at the start vs as it played out (committed path minus by-the-book path)');
console.log('weeks ahead | chains | mean predicted | mean played out | error (played out − predicted): mean, sd, mean miss, range');
for (let k = 1; k <= 7; k++) {
	const r = rows.filter((x) => x.k === k); if (!r.length) continue;
	const d = r.map((x) => x.realised - x.predicted), m = d.reduce((s, x) => s + x, 0) / d.length;
	const sd = Math.sqrt(d.reduce((s, x) => s + (x - m) ** 2, 0) / d.length), mae = d.reduce((s, x) => s + Math.abs(x), 0) / d.length;
	const mp = r.reduce((s, x) => s + x.predicted, 0) / r.length, mr = r.reduce((s, x) => s + x.realised, 0) / r.length;
	console.log(`${String(k).padEnd(11)} | ${String(r.length).padEnd(6)} | ${mp.toFixed(2).padStart(14)} | ${mr.toFixed(2).padStart(15)} | ${(m >= 0 ? '+' : '') + m.toFixed(2)}, ${sd.toFixed(2)}, ${mae.toFixed(2)}, ${Math.min(...d).toFixed(1)} to ${Math.max(...d).toFixed(1)}`);
}
console.log('\nEach chain: predicted → played out');
for (const r of rows.sort((a, b) => a.k - b.k || a.u - b.u)) console.log(`  +${r.k} GW${r.u}→WC GW${r.u + r.k}: ${r.predicted.toFixed(1)} → ${r.realised.toFixed(1)}`);
