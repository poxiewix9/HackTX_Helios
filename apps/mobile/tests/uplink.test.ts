/**
 * Uplink: pins queue while isolated, upload when any device has reach, and the
 * "uploaded" mark travels through the CRDT so other gateways do not re-send.
 */
import { MeshDoc } from "@/core/crdt";
import { DeviceKey } from "@/core/signing";
import { Uplink } from "@/core/uplink";

function fakeFetch(status = 201) {
  const calls: { url: string; body: unknown }[] = [];
  const fn = ((url: string, init?: { body?: string }) => {
    calls.push({ url, body: init?.body ? JSON.parse(init.body) : null });
    return Promise.resolve({ ok: status < 400, status } as Response);
  }) as unknown as typeof fetch;
  return { fn, calls };
}

describe("Uplink", () => {
  it("does nothing while offline and uploads everything pending once online", async () => {
    const doc = new MeshDoc();
    const key = DeviceKey.fromSeed("isolated-phone");
    const p1 = key.makeReport({
      lon: -156.67,
      lat: 20.87,
      report_type: "hazard_pin",
      hazard_class: "fire_front",
    });
    const p2 = key.makeReport({
      lon: -156.68,
      lat: 20.88,
      report_type: "hazard_pin",
      hazard_class: "smoke",
    });
    doc.addPin(p1);
    doc.addPin(p2);
    const { fn, calls } = fakeFetch();
    const up = new Uplink(doc, {
      blackboardUrl: "http://bb:8001/",
      fetchFn: fn,
      scenarioT: () => 120,
    });

    expect((await up.flush(false)).uploaded).toEqual([]);
    expect(calls).toHaveLength(0);
    expect(doc.pendingUpload()).toHaveLength(2);

    const r = await up.flush(true);
    expect(r.uploaded.sort()).toEqual([p1.id, p2.id].sort());
    expect(calls.map((c) => c.url)).toEqual([
      "http://bb:8001/reports?scenario_t=120",
      "http://bb:8001/reports?scenario_t=120",
    ]);
    expect((calls[0]?.body as { signature: string }).signature).toBe(p1.signature);
    expect(doc.pendingUpload()).toHaveLength(0);

    // Second flush: nothing left.
    expect((await up.flush(true)).uploaded).toEqual([]);
    expect(calls).toHaveLength(2);
  });

  it("the uploaded mark propagates: a second gateway does not re-send", async () => {
    const a = new MeshDoc();
    const b = new MeshDoc();
    const pin = DeviceKey.fromSeed("x").makeReport({
      lon: -156.67,
      lat: 20.87,
      report_type: "hazard_pin",
      hazard_class: "flood_water",
    });
    a.addPin(pin);
    b.applyUpdate(a.fullState());
    const fa = fakeFetch();
    const fb = fakeFetch();
    await new Uplink(a, { blackboardUrl: "http://bb", fetchFn: fa.fn }).flush(true);
    expect(fa.calls).toHaveLength(1);
    b.applyUpdate(a.fullState()); // the "uploaded" map arrives over the mesh
    await new Uplink(b, { blackboardUrl: "http://bb", fetchFn: fb.fn }).flush(true);
    expect(fb.calls).toHaveLength(0);
  });

  it("keeps a pin pending when the blackboard rejects it, and retries later", async () => {
    const doc = new MeshDoc();
    doc.addPin(
      DeviceKey.fromSeed("y").makeReport({
        lon: -156.67,
        lat: 20.87,
        report_type: "hazard_pin",
        hazard_class: "debris",
      }),
    );
    const bad = fakeFetch(503);
    const up = new Uplink(doc, { blackboardUrl: "http://bb", fetchFn: bad.fn });
    const r = await up.flush(true);
    expect(r.failed).toHaveLength(1);
    expect(r.failed[0]?.error).toMatch(/503/);
    expect(doc.pendingUpload()).toHaveLength(1);
    const good = fakeFetch(201);
    const up2 = new Uplink(doc, { blackboardUrl: "http://bb", fetchFn: good.fn });
    expect((await up2.flush(true)).uploaded).toHaveLength(1);
    expect(doc.pendingUpload()).toHaveLength(0);
  });
});
