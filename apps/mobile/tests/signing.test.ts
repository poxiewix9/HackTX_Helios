import { readFileSync } from "node:fs";

import type { CommunityReport } from "@arco/schemas";

import {
  DeviceKey,
  canonicalBytesOf,
  fromBase64,
  pyFloat,
  toBase64,
  verifyReport,
} from "@/core/signing";

interface Fixture {
  seed: string;
  device_pubkey: string;
  reporter_hash: string;
  reports: { report: CommunityReport; canonical_b64: string }[];
}

const fixture = JSON.parse(
  readFileSync(new URL("./fixtures/signed_reports.json", import.meta.url), "utf8"),
) as Fixture;

describe("signing contract (cross-checked against the Python reference fixture)", () => {
  const key = DeviceKey.fromSeed(fixture.seed);

  it("derives the same key from the same seed", () => {
    expect(key.pubkeyB64).toBe(fixture.device_pubkey);
    expect(key.reporterHash).toBe(fixture.reporter_hash);
  });

  it("produces byte-identical canonical JSON", () => {
    for (const { report, canonical_b64 } of fixture.reports) {
      expect(toBase64(canonicalBytesOf(report))).toBe(canonical_b64);
    }
  });

  it("re-signs the fixture reports to the identical signature (Ed25519 is deterministic)", () => {
    for (const { report } of fixture.reports) {
      const mine = key.makeReport({
        id: report.id,
        lon: report.geom.coordinates[0],
        lat: report.geom.coordinates[1],
        report_type: report.report_type,
        hazard_class: report.hazard_class ?? null,
        target_hazard_id: report.target_hazard_id ?? null,
        note: report.note ?? null,
        created_at: new Date(report.created_at),
      });
      expect(mine.signature).toBe(report.signature);
      expect(mine.device_pubkey).toBe(report.device_pubkey);
      expect(mine.reporter_hash).toBe(report.reporter_hash);
    }
  });

  it("verifies the Python-signed reports and rejects tampering", () => {
    for (const { report } of fixture.reports) {
      expect(verifyReport(report)).toBe(true);
      expect(verifyReport({ ...report, note: "moved" })).toBe(false);
      expect(
        verifyReport({
          ...report,
          geom: {
            type: "Point",
            coordinates: [report.geom.coordinates[0] + 0.001, report.geom.coordinates[1]],
          },
        }),
      ).toBe(false);
      // Swapping in another device's pubkey breaks the reporter hash check.
      const other = DeviceKey.fromSeed("someone-else");
      expect(verifyReport({ ...report, device_pubkey: other.pubkeyB64 })).toBe(false);
      expect(
        verifyReport({
          ...report,
          device_pubkey: other.pubkeyB64,
          reporter_hash: other.reporterHash,
        }),
      ).toBe(false);
    }
  });

  it("round-trips a freshly generated key through persistence", () => {
    const k = DeviceKey.generate();
    const k2 = DeviceKey.fromSecretB64(k.secretB64());
    expect(k2.pubkeyB64).toBe(k.pubkeyB64);
    const r = k2.makeReport({
      lon: -156.67,
      lat: 20.87,
      report_type: "hazard_pin",
      hazard_class: "flood_water",
    });
    expect(verifyReport(r)).toBe(true);
    expect(fromBase64(r.signature)).toHaveLength(64);
  });

  it("formats floats like Python repr", () => {
    expect(pyFloat(20)).toBe("20.0");
    expect(pyFloat(-156.68)).toBe("-156.68");
    expect(pyFloat(20.876543)).toBe("20.876543");
    expect(pyFloat(0.00005)).toBe("5e-05");
    expect(pyFloat(0.000001)).toBe("1e-06");
    expect(pyFloat(0.0001)).toBe("0.0001");
  });
});
