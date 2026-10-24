#!/usr/bin/env node
/**
 * Record a replay file from a RUNNING stack (after a scenario has been played):
 *   node scripts/record-replay.mjs [--out public/recorded/<scenario>.replay.json]
 *
 * It captures the real blackboard outbox (ws /ws/blackboard?since_seq=0: hazards, edges,
 * graph, reports, agent events, clock, network, routes) until the stream goes idle, a few
 * seconds of benchmark telemetry, and the REST snapshot needed to draw the map. Nothing is
 * synthesised here: an empty stack produces an (honestly) empty replay.
 *
 * Env: BLACKBOARD_URL (http://localhost:8001), SIMULATOR_URL (http://localhost:8004),
 *      BENCHMARK_URL (http://localhost:8005)
 */
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import WebSocket from "ws";

const BLACKBOARD_URL = process.env.BLACKBOARD_URL ?? "http://localhost:8001";
const SIMULATOR_URL = process.env.SIMULATOR_URL ?? "http://localhost:8004";
const BENCHMARK_URL = process.env.BENCHMARK_URL ?? "http://localhost:8005";
const IDLE_MS = 3000;
const TELEMETRY_MS = 6000;

const args = process.argv.slice(2);
const outIdx = args.indexOf("--out");

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function toWs(httpUrl) {
  return httpUrl.replace(/^http/, "ws");
}

/** Collect text frames until `idleMs` passes without one (or `maxMs` total). */
function collect(url, { idleMs, maxMs }) {
  return new Promise((resolve, reject) => {
    const out = [];
    const ws = new WebSocket(url);
    let idle = null;
    const done = () => {
      clearTimeout(idle);
      clearTimeout(hard);
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      resolve(out);
    };
    const bump = () => {
      clearTimeout(idle);
      idle = setTimeout(done, idleMs);
    };
    const hard = setTimeout(done, maxMs);
    ws.on("open", bump);
    ws.on("message", (data) => {
      try {
        out.push(JSON.parse(data.toString()));
      } catch {
        /* skip non-JSON */
      }
      bump();
    });
    ws.on("error", (err) => {
      clearTimeout(idle);
      clearTimeout(hard);
      reject(err);
    });
    ws.on("close", () => resolve(out));
  });
}

async function main() {
  const scenario = await getJson(`${SIMULATOR_URL}/scenario`);
  const bbox = scenario.bbox.join(",");
  const [edges, safeZones] = await Promise.all([
    getJson(`${BLACKBOARD_URL}/edges?bbox=${bbox}`),
    getJson(`${BLACKBOARD_URL}/safe-zones`),
  ]);
  process.stderr.write(
    `scenario ${scenario.id}: ${edges.length} edges, ${safeZones.length} safe zones\n`,
  );

  const all = await collect(`${toWs(BLACKBOARD_URL)}/ws/blackboard?since_seq=0`, {
    idleMs: IDLE_MS,
    maxMs: 120_000,
  });
  // Keep only the most recent scenario run: everything from the last `clock.tick` at t=0
  // (published by the simulator on load) onwards. Earlier runs would interleave by t.
  let start = 0;
  for (let i = 0; i < all.length; i += 1) {
    const m = all[i];
    if (m.type === "clock.tick" && m.scenario_t === 0) start = i;
  }
  const messages = all.slice(start);
  process.stderr.write(
    `blackboard: ${all.length} messages in outbox, ${messages.length} in the last run\n`,
  );

  let telemetry = [];
  try {
    telemetry = await collect(`${toWs(BENCHMARK_URL)}/ws/telemetry`, {
      idleMs: TELEMETRY_MS,
      maxMs: TELEMETRY_MS,
    });
  } catch (err) {
    process.stderr.write(`benchmark telemetry unavailable: ${String(err)}\n`);
  }
  process.stderr.write(`telemetry: ${telemetry.length} messages\n`);

  // Keep the file small: the initial edge set is in `edges`; edge.updated events carry
  // only the edges whose status/cost changed, which is what the replay needs.
  const counts = {};
  for (const m of messages) counts[m.type] = (counts[m.type] ?? 0) + 1;
  process.stderr.write(`${JSON.stringify(counts)}\n`);

  // The REST snapshot is the *end* state of the run. The replay starts at t=0, so edges are
  // normalised to open here; every later status comes from a recorded edge.updated event.
  const edgesAtStart = edges.map((e) => ({
    ...e,
    status: "open",
    current_cost: e.base_cost,
    current_load: 0,
  }));
  const file = {
    format: "arco.replay/1",
    recorded_at: new Date().toISOString(),
    label: scenario.label,
    notes:
      "Recorded from a live ARCO stack. edges = road graph normalised to open at t=0; " +
      "statuses, routes, hazards and agent reasoning are the recorded bus messages, unmodified.",
    scenario,
    edges: edgesAtStart,
    safe_zones: safeZones,
    messages,
    telemetry,
  };
  const out = outIdx >= 0 ? args[outIdx + 1] : `public/recorded/${scenario.id}.replay.json`;
  await mkdir(dirname(out), { recursive: true });
  const text = JSON.stringify(file);
  await writeFile(out, text);
  process.stderr.write(`wrote ${out} (${(text.length / 1024 / 1024).toFixed(2)} MB)\n`);
}

main().catch((err) => {
  process.stderr.write(`record-replay failed: ${String(err)}\n`);
  process.exit(1);
});
