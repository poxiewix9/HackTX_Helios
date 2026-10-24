import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import bundle from "../../schema/arco.schema.json";
import type { Hazard, Scenario, TelemetrySample } from "./index";
import { HAZARD_CLASS_IDS, SCHEMA_VERSION, isMessageOfType } from "./index";
import type { AnyMessage } from "./index";

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(bundle);

function validator(name: string) {
  const v = ajv.getSchema(`${bundle.$id}#/$defs/${name}`);
  if (!v) throw new Error(`no schema ${name}`);
  return v;
}

const square = {
  type: "Polygon" as const,
  coordinates: [
    [
      [0, 0],
      [0, 0.001],
      [0.001, 0.001],
      [0.001, 0],
      [0, 0],
    ],
  ],
} satisfies Hazard["geom"];

const hazard: Hazard = {
  id: "7c1a4f0e-6a2b-4d8e-9f10-1234567890ab",
  geom: square,
  hazard_class: "fire_front",
  confidence: 0.9,
  source: "macro_vision",
  stream: "macro",
  camera_id: "tile_0",
  first_seen: "2026-10-24T12:00:00Z",
  last_seen: "2026-10-24T12:00:00Z",
  ttl_s: 600,
  status: "active",
  severity: 1,
  velocity_m_s: 0.4,
};

describe("bundle", () => {
  it("has the schema version the TS package was generated from", () => {
    expect(bundle.version).toBe(SCHEMA_VERSION);
  });

  it("enumerates the same hazard classes as the taxonomy JSON", () => {
    const enumIds = (bundle.$defs.HazardClass as { enum: string[] }).enum;
    expect([...HAZARD_CLASS_IDS].sort()).toEqual([...enumIds].sort());
  });
});

describe("Hazard", () => {
  it("accepts a typed hazard", () => {
    const v = validator("Hazard");
    expect(v(hazard), JSON.stringify(v.errors)).toBe(true);
  });

  it("rejects confidence > 1", () => {
    expect(validator("Hazard")({ ...hazard, confidence: 1.5 })).toBe(false);
  });

  it("rejects extra properties (no raw frames riding along)", () => {
    expect(validator("Hazard")({ ...hazard, raw_frame_b64: "..." })).toBe(false);
  });

  it("rejects an unknown class", () => {
    expect(validator("Hazard")({ ...hazard, hazard_class: "lava" })).toBe(false);
  });
});

describe("TelemetrySample", () => {
  const sample: TelemetrySample = {
    mode: "live",
    pipeline: "int8_split",
    stream: "micro",
    frame_id: 7,
    wall_t: "2026-10-24T12:00:00Z",
    stage_ms: { head: 4.1, network: 2.0, tail: 11.2 },
    total_ms: 17.3,
    fps: 29.5,
    bytes_sent: 4096,
    dropped_frames: 0,
  };

  it("requires mode so the LIVE/RECORDED badge can never be omitted", () => {
    const { mode: _mode, ...noMode } = sample;
    expect(validator("TelemetrySample")(sample)).toBe(true);
    expect(validator("TelemetrySample")(noMode)).toBe(false);
  });

  it("rejects stages outside the pipeline", () => {
    expect(validator("TelemetrySample")({ ...sample, stage_ms: { cloud_upload: 1 } })).toBe(false);
  });
});

describe("messages", () => {
  // Parsed from the wire, so TS only knows it is *some* message.
  const msg = JSON.parse(
    JSON.stringify({
      type: "hazard.created",
      scenario_t: 12.5,
      wall_t: "2026-10-24T12:00:00Z",
      seq: 1,
      hazard,
    }),
  ) as AnyMessage;

  it("validates against AnyMessage and narrows by type", () => {
    const v = validator("AnyMessage");
    expect(v(msg), JSON.stringify(v.errors)).toBe(true);
    if (isMessageOfType(msg, "hazard.created")) {
      expect(msg.hazard.hazard_class).toBe("fire_front");
    } else {
      throw new Error("narrowing failed");
    }
    expect(isMessageOfType(msg, "routes.updated")).toBe(false);
  });

  it("rejects unknown message types", () => {
    expect(validator("AnyMessage")({ ...msg, type: "frame.raw" })).toBe(false);
  });
});

describe("Scenario", () => {
  it("only accepts the honest simulation label", () => {
    const v = validator("Scenario");
    const minimal: Partial<Scenario> = { label: "Simulated on real event geometry" };
    // Missing required fields -> invalid, but the label error specifically must appear
    // when the label is wrong.
    expect(v({ ...minimal, label: "Live inference replay" as Scenario["label"] })).toBe(false);
    expect(JSON.stringify(v.errors)).toContain("label");
  });
});
