/**
 * Dashboard mesh demo: a pin dropped on the far phone (no cellular anywhere) reaches the
 * gateway over several simulated hops; nothing is uploaded until the backhaul returns,
 * then the gateway posts the *signed* pin to the blackboard exactly once.
 */
import { verifyReport } from "@arco/mobile/core";
import type { CommunityReport } from "@arco/schemas";

import { MeshDemo, defaultDevices } from "@/components/mesh/meshDemo";

function fakeFetch() {
  const posted: CommunityReport[] = [];
  const fn = ((url: string, init?: { body?: string }) => {
    if (url.endsWith("/reports") || url.includes("/reports?")) {
      posted.push(JSON.parse(init?.body ?? "{}") as CommunityReport);
    }
    return Promise.resolve({ ok: true, status: 201 } as Response);
  }) as unknown as typeof fetch;
  return { fn, posted };
}

describe("MeshDemo", () => {
  it("pin from an isolated phone reaches the gateway over the mesh and uploads only when cellular is back", async () => {
    let t = 0;
    const { fn, posted } = fakeFetch();
    const demo = new MeshDemo({
      devices: defaultDevices([-156.675, 20.88]),
      gatewayId: "gateway",
      blackboardUrl: "/api/bb",
      fetchFn: fn,
      now: () => t,
      latencyMs: 20,
      loss: 0.15,
      scenarioT: () => 300,
    });
    demo.start();
    demo.setCellular(false);
    const snap0 = demo.snapshot();
    expect(snap0.devices.find((d) => d.id === "gateway")?.connectivity).toBe("mesh");
    expect(snap0.devices.every((d) => d.connectivity !== "cell")).toBe(true);

    const pin = demo.dropPin("phone-5", "fire_front", "test pin");
    expect(pin).not.toBeNull();
    if (!pin) return;
    expect(verifyReport(pin)).toBe(true);

    // Run the simulated radio for a while: gossip + flooding across 6 hops with 15% loss.
    const run = (ms: number) => {
      for (let i = 0; i < ms; i += 10) {
        t += 10;
        demo.tick(t);
        if (t % 1000 === 0) for (const n of demo.nodes.values()) n.sync.gossip();
      }
    };
    run(3000);
    const gw = demo.nodes.get("gateway");
    expect(gw?.doc.pin(pin.id)?.signature).toBe(pin.signature);
    expect(demo.snapshot().devices.every((d) => d.pins === 1)).toBe(true);

    // Backhaul down: nothing leaves the mesh.
    expect(await demo.flushUplink()).toBe(0);
    expect(posted).toHaveLength(0);

    // Backhaul up: the gateway uploads the signed pin with the scenario time, once.
    demo.setCellular(true);
    expect(demo.snapshot().devices.find((d) => d.id === "gateway")?.connectivity).toBe("cell");
    expect(await demo.flushUplink()).toBe(1);
    expect(posted).toHaveLength(1);
    expect(posted[0]?.id).toBe(pin.id);
    expect(posted[0]?.signature).toBe(pin.signature);
    expect(await demo.flushUplink()).toBe(0);
    expect(demo.snapshot().uploaded).toBe(1);
    demo.stop();
  });

  it("partition isolates the far cluster and heal merges it", () => {
    let t = 0;
    const demo = new MeshDemo({
      devices: defaultDevices([-156.675, 20.88]),
      gatewayId: "gateway",
      blackboardUrl: "/api/bb",
      fetchFn: fakeFetch().fn,
      now: () => t,
      latencyMs: 20,
      loss: 0,
    });
    demo.start();
    const run = (ms: number) => {
      for (let i = 0; i < ms; i += 10) {
        t += 10;
        demo.tick(t);
        if (t % 500 === 0) for (const n of demo.nodes.values()) n.sync.gossip();
      }
    };
    run(200);
    demo.setPartition(true, ["gateway", "phone-1", "phone-2", "phone-3"]);
    const pin = demo.dropPin("phone-5", "smoke");
    run(1500);
    expect(demo.nodes.get("phone-4")?.doc.pin(pin?.id ?? "")).toBeDefined();
    expect(demo.nodes.get("gateway")?.doc.pin(pin?.id ?? "")).toBeUndefined();
    expect(demo.snapshot().partitioned).toBe(true);
    demo.setPartition(false);
    run(1500);
    expect(demo.nodes.get("gateway")?.doc.pin(pin?.id ?? "")).toBeDefined();
    demo.stop();
  });
});
