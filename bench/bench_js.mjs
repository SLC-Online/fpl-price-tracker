// Run the JavaScript port on the same frozen fixture (in Node, which uses
// Chrome's V8 engine).
//   node bench/bench_js.mjs bench/fixture_403851.json bench/result_js.json
import { readFileSync, writeFileSync } from "node:fs";
import { loadFixture, optimize, summarise } from "./engine.mjs";

const fx = JSON.parse(readFileSync(process.argv[2], "utf8"));
const players = loadFixture(fx);
const t0 = performance.now();
const { base, ranked, evaluated } = optimize(players, fx.squad_ids, fx.weights, fx.bank, fx.free_transfers, 2, 20);
const secs = (performance.now() - t0) / 1000;
writeFileSync(process.argv[3], JSON.stringify({ engine: "node", seconds: secs, base, evaluated, ranked: summarise(ranked) }));
console.log(`node: ${secs.toFixed(1)}s, base ${base}, squads evaluated ${evaluated}`);
