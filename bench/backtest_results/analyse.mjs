import { readFileSync } from 'node:fs';
const decays = ['1', '0.95', '0.9', '0.85', '0.8', '0.75', '0.7'];
const file = (d) => (d === '0.85' ? '/tmp/wc_curve_403851.jsonl' : `/tmp/wc_curve_403851_d${d}.jsonl`);
const last = (d) => { const m = new Map(); for (const r of readFileSync(file(d), 'utf8').trim().split('\n').map(JSON.parse)) m.set(`${r.u}:${r.wcOffset}`, r); return m; };
console.log('A. Plain backtest by decay: which week comes out best, and the curve relative to "play it now"');
console.log('decay | times each offset ranked 1st (+0..+7) | avg gain as % of that start\'s +0      | GW12 start: top 3 | pending');
for (const d of decays) {
	const m = last(d); const best = Array(8).fill(0), rel = Array(8).fill(0), cnt = Array(8).fill(0); let pending = 0;
	for (const r of m.values()) if (r.status !== 'Optimal') pending++;
	for (let u = 2; u <= 12; u++) {
		const b = m.get(`${u}:null`); if (!b) continue;
		const g = []; for (let k = 0; k < 8; k++) { const r = m.get(`${u}:${k}`); if (r) g[k] = r.objective - b.objective; }
		const bk = g.indexOf(Math.max(...g.filter((x) => x !== undefined))); best[bk]++;
		for (let k = 0; k < 8; k++) if (g[k] !== undefined && g[0] > 0) { rel[k] += g[k] / g[0]; cnt[k]++; }
	}
	const b12 = m.get('12:null'); const top = [0,1,2,3,4,5,6,7].map((k) => [k, m.get(`12:${k}`).objective - b12.objective]).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `GW${12 + k} +${v.toFixed(2)}`).join(', ');
	console.log(`${d.padEnd(5)} | ${best.join(' ').padEnd(37)} | ${rel.map((s, k) => Math.round((100 * s) / cnt[k]) + '%').join(' ').padEnd(38)} | ${top} | ${pending}`);
}

console.log('\nB. Commit and follow (decay 0.85): the committed wildcard\'s worth over the weeks both estimates cover, no decay');
const rs = readFileSync('/tmp/wc_follow_403851_d0.85.jsonl', 'utf8').trim().split('\n').map(JSON.parse);
const shared = (s, from, to) => { let v = 0; for (let t = from; t <= to; t++) v += (s.A.weeks[t].points - s.B.weeks[t].points) - (s.A.weeks[t].hits - s.B.weeks[t].hits); return v; };
console.log('weeks ahead | chains | predicted at start | at the wildcard week | change: mean, spread (sd), range | same sign');
for (let k = 1; k <= 7; k++) {
	const c = rs.filter((r) => r.k === k); if (!c.length) continue;
	const pairs = c.map((r) => { const s0 = r.steps[0], sk = r.steps[k]; return [shared(s0, k, 7), shared(sk, 0, 7 - k)]; });
	const d = pairs.map(([p, a]) => a - p); const mean = d.reduce((s, x) => s + x, 0) / d.length; const sd = Math.sqrt(d.reduce((s, x) => s + (x - mean) ** 2, 0) / d.length);
	const mp = pairs.reduce((s, x) => s + x[0], 0) / pairs.length, ma = pairs.reduce((s, x) => s + x[1], 0) / pairs.length;
	console.log(`${String(k).padEnd(11)} | ${String(c.length).padEnd(6)} | ${mp.toFixed(2).padStart(18)} | ${ma.toFixed(2).padStart(20)} | ${mean >= 0 ? '+' : ''}${mean.toFixed(2)}, ${sd.toFixed(2)}, ${Math.min(...d).toFixed(1)} to ${Math.max(...d).toFixed(1)} | ${pairs.filter(([p, a]) => p * a > 0).length}/${pairs.length}`);
}
console.log('\nEach chain, week by week (shared-window worth, no decay):');
for (const r of rs.filter((r) => r.k >= 3).sort((a, b) => a.u - b.u || a.k - b.k)) {
	console.log(`  GW${r.u} → WC GW${r.u + r.k}: ` + r.steps.map((s, j) => shared(s, r.k - j, 7 - j).toFixed(1)).join(' → '));
}
