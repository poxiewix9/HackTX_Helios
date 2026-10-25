/**
 * Signed community reports — the TypeScript side of the signing contract defined by the
 * Consensus agent (`services/agents/arco_agents/reports.py`, HANDOFFS 6.1).
 *
 *   signature = Ed25519( canonical_bytes )
 *   canonical_bytes = JSON with sorted keys, no spaces, of
 *     {id, lon, lat, report_type, hazard_class, target_hazard_id, reporter_hash, created_at, note}
 *   lon/lat rounded to 6 dp, created_at = ISO-8601 UTC with milliseconds and "Z",
 *   null for absent hazard_class / target_hazard_id / note, ids as lowercase UUID strings.
 *   reporter_hash = sha256(raw 32-byte pubkey) hex; device_pubkey / signature = base64.
 *
 * `tests/signing.test.ts` checks these bytes against a fixture produced by the Python
 * reference implementation (same seed → same key → byte-identical signature).
 */
import * as ed from "@noble/ed25519";
import { sha256, sha512 } from "@noble/hashes/sha2.js";

import type { CommunityReport, HazardClass } from "@arco/schemas";

// noble v3 needs the hash wired explicitly (pure JS; works in React Native + node).
ed.hashes.sha512 = sha512;
ed.hashes.sha512Async = (m: Uint8Array) => Promise.resolve(sha512(m));

export type ReportType = CommunityReport["report_type"];

export interface CanonicalFields {
  id: string;
  lon: number;
  lat: number;
  report_type: ReportType;
  hazard_class: HazardClass | null;
  target_hazard_id: string | null;
  reporter_hash: string;
  created_at: string;
  note: string | null;
}

const textEncoder = new TextEncoder();

export function round6(x: number): number {
  return Math.round(x * 1e6) / 1e6;
}

/** `Date.toISOString()` already matches the contract (ms precision, `Z`). */
export function isoMsZ(d: Date): string {
  return d.toISOString();
}

/**
 * Python's `json.dumps(round(x, 6))` prints floats with `repr`, which for a 6-dp rounded
 * value is the shortest round-trip form — the same thing JS `String(number)` produces.
 * Integral values differ (`20.0` vs `20`), so force one decimal in that case.
 */
export function pyFloat(x: number): string {
  if (Object.is(x, -0)) return "-0.0";
  if (Number.isInteger(x)) return `${x}.0`;
  // Python repr switches to exponent form below 1e-4 (`5e-05`), JS only below 1e-6 and
  // with a bare exponent (`5e-5`); normalise to Python's two-digit exponent.
  if (Math.abs(x) < 1e-4) {
    const [mant, exp] = x.toExponential().split("e");
    const e = Number(exp);
    return `${mant ?? "0"}e${e < 0 ? "-" : "+"}${String(Math.abs(e)).padStart(2, "0")}`;
  }
  return String(x);
}

/** Byte-identical to `arco_agents.reports.canonical_bytes`. */
export function canonicalBytes(f: CanonicalFields): Uint8Array {
  // Reports that went through the blackboard carry Pydantic's microsecond timestamp
  // (`.123000Z`); the contract signs the millisecond form, so normalise first.
  const createdAt = isoMsZ(new Date(f.created_at));
  const parts: string[] = [
    `"created_at":${JSON.stringify(createdAt)}`,
    `"hazard_class":${f.hazard_class === null ? "null" : JSON.stringify(f.hazard_class)}`,
    `"id":${JSON.stringify(f.id.toLowerCase())}`,
    `"lat":${pyFloat(round6(f.lat))}`,
    `"lon":${pyFloat(round6(f.lon))}`,
    `"note":${f.note === null ? "null" : JSON.stringify(f.note)}`,
    `"report_type":${JSON.stringify(f.report_type)}`,
    `"reporter_hash":${JSON.stringify(f.reporter_hash)}`,
    `"target_hazard_id":${f.target_hazard_id === null ? "null" : JSON.stringify(f.target_hazard_id.toLowerCase())}`,
  ];
  // Keys above are already in sorted order (Python sort_keys=True sorts by code point).
  return textEncoder.encode(`{${parts.join(",")}}`);
}

export function canonicalBytesOf(r: CommunityReport): Uint8Array {
  return canonicalBytes({
    id: r.id,
    lon: r.geom.coordinates[0],
    lat: r.geom.coordinates[1],
    report_type: r.report_type,
    hazard_class: r.hazard_class ?? null,
    target_hazard_id: r.target_hazard_id ?? null,
    reporter_hash: r.reporter_hash,
    created_at: r.created_at,
    note: r.note ?? null,
  });
}

export function toBase64(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function fromBase64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function uuid4(random: (n: number) => Uint8Array = randomBytes): string {
  const b = random(16);
  b[6] = ((b[6] ?? 0) & 0x0f) | 0x40;
  b[8] = ((b[8] ?? 0) & 0x3f) | 0x80;
  const h = toHex(b);
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function randomBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  globalThis.crypto.getRandomValues(out);
  return out;
}

export interface MakeReportInput {
  lon: number;
  lat: number;
  report_type: ReportType;
  hazard_class?: HazardClass | null;
  target_hazard_id?: string | null;
  note?: string | null;
  created_at?: Date;
  id?: string;
}

/** A per-device Ed25519 key. The private half never leaves the device. */
export class DeviceKey {
  readonly publicKey: Uint8Array;
  readonly pubkeyB64: string;
  readonly reporterHash: string;

  private constructor(private readonly secret: Uint8Array) {
    this.publicKey = ed.getPublicKey(secret);
    this.pubkeyB64 = toBase64(this.publicKey);
    this.reporterHash = toHex(sha256(this.publicKey));
  }

  static generate(random: (n: number) => Uint8Array = randomBytes): DeviceKey {
    return new DeviceKey(random(32));
  }

  /** Deterministic key: `sha256(seed)` is the 32-byte secret (same as the Python side). */
  static fromSeed(seed: string | Uint8Array): DeviceKey {
    const bytes = typeof seed === "string" ? textEncoder.encode(seed) : seed;
    return new DeviceKey(sha256(bytes));
  }

  /** Restore from a persisted 32-byte secret (base64). */
  static fromSecretB64(b64: string): DeviceKey {
    const s = fromBase64(b64);
    if (s.length !== 32) throw new Error("device secret must be 32 bytes");
    return new DeviceKey(s);
  }

  secretB64(): string {
    return toBase64(this.secret);
  }

  sign(data: Uint8Array): string {
    return toBase64(ed.sign(data, this.secret));
  }

  makeReport(input: MakeReportInput): CommunityReport {
    const id = (input.id ?? uuid4()).toLowerCase();
    const createdAt = isoMsZ(input.created_at ?? new Date());
    const lon = round6(input.lon);
    const lat = round6(input.lat);
    const hazardClass = input.hazard_class ?? null;
    const target = input.target_hazard_id ?? null;
    const note = input.note ?? null;
    const data = canonicalBytes({
      id,
      lon,
      lat,
      report_type: input.report_type,
      hazard_class: hazardClass,
      target_hazard_id: target,
      reporter_hash: this.reporterHash,
      created_at: createdAt,
      note,
    });
    return {
      id,
      geom: { type: "Point", coordinates: [lon, lat] },
      report_type: input.report_type,
      hazard_class: hazardClass,
      target_hazard_id: target,
      note,
      created_at: createdAt,
      reporter_hash: this.reporterHash,
      device_pubkey: this.pubkeyB64,
      signature: this.sign(data),
    };
  }
}

/** True iff the signature verifies under the embedded pubkey and the hash matches it. */
export function verifyReport(r: CommunityReport): boolean {
  try {
    const pub = fromBase64(r.device_pubkey);
    if (pub.length !== 32) return false;
    if (toHex(sha256(pub)) !== r.reporter_hash) return false;
    return ed.verify(fromBase64(r.signature), canonicalBytesOf(r), pub);
  } catch {
    return false;
  }
}
