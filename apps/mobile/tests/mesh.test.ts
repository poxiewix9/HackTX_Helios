/**
 * CRDT + SimulatedMeshTransport: convergence under packet loss, partition-and-heal, and
 * "a pin dropped on one device appears on another with cellular disabled".
 * Time is virtual: the network is flushed by hand and gossip rounds are triggered by hand.
 */
import type { Route } from "@arco/schemas";

import { MeshDoc } from "@/core/crdt";
import { MeshNode } from "@/core/node";
import { SimulatedMeshNetwork, seededRandom } from "@/core/simulatedMesh";
import type { SimulatedMeshTransport } from "@/core/simulatedMesh";
import { DeviceKey } from "@/core/signing";
import { MeshSync } from "@/core/sync";

interface Device {
  id: string;
  transport: SimulatedMeshTransport;
  doc: MeshDoc;
  sync: MeshSync;
}

function makeNetwork(opts: { loss?: number; range?: number } = {}) {
  let t = 0;
  const net = new SimulatedMeshNetwork({
    rangeM: opts.range ?? 100,
    loss: opts.loss ?? 0,
    latencyMs: 10,
    random: seededRandom(42),
    now: () => t,
  });
  const devices: Device[] = [];
  // A chain a-b-c-d-e, 80 m apart: only neighbours are in range (multi-hop is required).
  for (const [i, id] of ["a", "b", "c", "d", "e"].entries()) {
    const transport = net.addDevice({ id, x: i * 80, y: 0, cellular: false });
    const doc = new MeshDoc();
    const sync = new MeshSync(doc, { gossipEveryMs: 0, fanout: 0, random: seededRandom(7) });
    sync.addTransport(transport);
    devices.push({ id, transport, doc, sync });
  }
  for (const d of devices) {
    d.transport.start();
    d.sync.start();
  }
  /** Advance virtual time by `ms`, delivering frames, running one gossip round per 100 ms. */
  const run = (ms: number) => {
    for (let i = 0; i < ms; i += 10) {
      t += 10;
      net.flush(t);
      if (t % 100 === 0) for (const d of devices) d.sync.gossip();
    }
    // Drain replies that the last round queued (one latency hop later).
    t += 10;
    net.flush(t);
  };
  const converged = () => {
    const sets = devices.map((d) =>
      d.doc
        .pins()
        .map((p) => p.id)
        .sort()
        .join(","),
    );
    return sets.every((s) => s === sets[0]);
  };
  return { net, devices, run, converged, now: () => t };
}

describe("MeshDoc", () => {
  it("merges pins from both sides regardless of order and rejects forged ones", () => {
    const a = new MeshDoc();
    const b = new MeshDoc();
    const ka = DeviceKey.fromSeed("a");
    const kb = DeviceKey.fromSeed("b");
    a.addPin(
      ka.makeReport({
        lon: -156.67,
        lat: 20.87,
        report_type: "hazard_pin",
        hazard_class: "fire_front",
      }),
    );
    b.addPin(
      kb.makeReport({
        lon: -156.68,
        lat: 20.88,
        report_type: "hazard_pin",
        hazard_class: "flood_water",
      }),
    );
    const ua = a.fullState();
    const ub = b.fullState();
    b.applyUpdate(ua);
    a.applyUpdate(ub);
    expect(a.pins().map((p) => p.id)).toEqual(b.pins().map((p) => p.id));
    expect(a.pins()).toHaveLength(2);

    // A forged report (signature from another key) is quarantined on receipt.
    const forged = {
      ...kb.makeReport({ lon: 0, lat: 0, report_type: "hazard_pin", hazard_class: "debris" }),
      note: "edited after signing",
    };
    const evil = new MeshDoc();
    expect(() => {
      evil.addPin(forged);
    }).toThrow(/invalid/);
    // Bypass the local check to simulate a malicious peer's update.
    evil.doc.getMap("pins").set(forged.id, forged);
    a.applyUpdate(evil.fullState());
    expect(a.pin(forged.id)).toBeUndefined();
    expect(a.rejected.map((r) => r.id)).toContain(forged.id);
  });

  it("keeps the newest route per crowd group", () => {
    const d = new MeshDoc();
    const base: Route = {
      id: "r1",
      crowd_group_id: "g1",
      origin_cell_id: "c1",
      safe_zone_id: "sz",
      edge_ids: [1, 2],
      path: {
        type: "LineString",
        coordinates: [
          [0, 0],
          [1, 1],
        ],
      },
      length_m: 100,
      cost: 10,
      group_size: 5,
      instruction: "go",
      computed_at: "2026-10-24T00:00:00.000Z",
      graph_version: 3,
      version: 1,
    };
    d.setRoute(base);
    d.setRoute({ ...base, id: "older", graph_version: 2, version: 9 });
    expect(d.route("g1")?.id).toBe("r1");
    d.setRoute({ ...base, id: "newer", graph_version: 3, version: 2 });
    expect(d.route("g1")?.id).toBe("newer");
  });
});

describe("SimulatedMeshTransport + MeshSync", () => {
  it("propagates a pin over multiple hops with cellular disabled on every device", () => {
    const { net, devices, run } = makeNetwork();
    net.setCellular(false);
    const [a, , , , e] = devices;
    if (!a || !e) throw new Error("setup");
    expect(net.hasCellular("a")).toBe(false);
    const key = DeviceKey.fromSeed("phone-a");
    const pin = key.makeReport({
      lon: -156.67,
      lat: 20.87,
      report_type: "hazard_pin",
      hazard_class: "fire_front",
      note: "Front St blocked",
    });
    a.doc.addPin(pin);
    expect(e.doc.pin(pin.id)).toBeUndefined();
    run(100); // flooding: 4 hops × 10 ms latency
    expect(e.doc.pin(pin.id)?.note).toBe("Front St blocked");
    expect(e.doc.pin(pin.id)?.signature).toBe(pin.signature);
    expect(net.stats.unreachable).toBe(0);
  });

  it("converges under 35% packet loss thanks to anti-entropy gossip", () => {
    const { net, devices, run, converged } = makeNetwork({ loss: 0.35 });
    for (const [i, d] of devices.entries()) {
      const k = DeviceKey.fromSeed(`k${i}`);
      for (let j = 0; j < 3; j++) {
        d.doc.addPin(
          k.makeReport({
            lon: -156.6 - i * 0.001,
            lat: 20.8 + j * 0.001,
            report_type: "hazard_pin",
            hazard_class: "debris",
          }),
        );
      }
    }
    expect(converged()).toBe(false);
    run(3000);
    expect(net.stats.dropped).toBeGreaterThan(0); // the loss really happened
    expect(converged()).toBe(true);
    const first = devices[0];
    if (!first) throw new Error("setup");
    expect(first.doc.pins()).toHaveLength(15);
  });

  it("partition-and-heal: both halves keep working apart, then merge to one state", () => {
    const { net, devices, run, converged } = makeNetwork();
    const [a, b, c, d, e] = devices;
    if (!a || !b || !c || !d || !e) throw new Error("setup");
    run(100); // let the initial state-vector announcements settle
    net.partitionDevices(["a", "b"]);
    expect(a.transport.peers()).toEqual(["b"]);
    expect(c.transport.peers()).toEqual(["d"]);

    const ka = DeviceKey.fromSeed("a");
    const ke = DeviceKey.fromSeed("e");
    const pinA = ka.makeReport({
      lon: -156.67,
      lat: 20.87,
      report_type: "hazard_pin",
      hazard_class: "fire_front",
    });
    const pinE = ke.makeReport({
      lon: -156.66,
      lat: 20.86,
      report_type: "hazard_pin",
      hazard_class: "flood_water",
    });
    a.doc.addPin(pinA);
    e.doc.addPin(pinE);
    run(500);
    // Each side converged internally, neither crossed the partition.
    expect(b.doc.pin(pinA.id)).toBeDefined();
    expect(c.doc.pin(pinE.id)).toBeDefined();
    expect(c.doc.pin(pinA.id)).toBeUndefined();
    expect(a.doc.pin(pinE.id)).toBeUndefined();
    expect(converged()).toBe(false);
    expect(net.stats.unreachable).toBe(0); // nothing was even attempted across the cut

    net.heal();
    run(500);
    expect(converged()).toBe(true);
    expect(a.doc.pins()).toHaveLength(2);
    expect(e.doc.pins()).toHaveLength(2);
  });

  it("a late joiner catches up from the first gossip round", () => {
    const { net, devices, run } = makeNetwork();
    const a = devices[0];
    if (!a) throw new Error("setup");
    a.doc.addPin(
      DeviceKey.fromSeed("a").makeReport({
        lon: -156.67,
        lat: 20.87,
        report_type: "hazard_pin",
        hazard_class: "fire_front",
      }),
    );
    run(200);
    const t = net.addDevice({ id: "f", x: 4 * 80 + 60, y: 0, cellular: false });
    const doc = new MeshDoc();
    const sync = new MeshSync(doc, { gossipEveryMs: 0, fanout: 0 });
    sync.addTransport(t);
    t.start();
    sync.start(); // announces its (empty) state vector to its neighbour "e"
    run(100);
    expect(doc.pins()).toHaveLength(1);
  });
});

describe("MeshNode", () => {
  it("reports connectivity as cell | mesh | isolated", () => {
    let t = 0;
    const net = new SimulatedMeshNetwork({ rangeM: 50, now: () => t });
    const ta = net.addDevice({ id: "a", x: 0, y: 0, cellular: true });
    const tb = net.addDevice({ id: "b", x: 500, y: 0, cellular: false });
    const a = new MeshNode({ id: "a", transports: [ta], sync: { gossipEveryMs: 0 } });
    const b = new MeshNode({ id: "b", transports: [tb], sync: { gossipEveryMs: 0 } });
    ta.start();
    tb.start();
    a.start();
    b.start();
    a.cellularUp = net.hasCellular("a");
    b.cellularUp = net.hasCellular("b");
    expect(a.connectivity()).toBe("cell");
    expect(b.connectivity()).toBe("isolated");
    net.setCellular(false);
    a.cellularUp = net.hasCellular("a");
    expect(a.connectivity()).toBe("isolated");
    net.moveDevice("b", 30, 0);
    expect(a.connectivity()).toBe("mesh");
    expect(b.connectivity()).toBe("mesh");
    t += 1;
    const pin = b.dropPin("smoke", null, [-156.67, 20.87]);
    net.flush(t);
    expect(a.doc.pin(pin.id)).toBeDefined();
  });
});
