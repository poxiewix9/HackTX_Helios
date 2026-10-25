/**
 * The shared document: a Yjs doc with two maps —
 *   pins   : report id  -> signed `CommunityReport` (immutable once written; LWW on the key)
 *   routes : crowd group -> `Route` (newer `version` wins; routes come from the agents and
 *            are relayed phone-to-phone so an isolated device still gets the latest one)
 * plus `uploaded`: report id -> ISO time, set by whichever device delivered the pin to the
 * blackboard, so the rest of the mesh stops trying.
 *
 * Yjs gives us convergence under any delivery order; `MeshSync` handles loss.
 */
import * as Y from "yjs";

import type { CommunityReport, Route } from "@arco/schemas";

import { verifyReport } from "./signing";

export type PinsListener = (pins: CommunityReport[]) => void;

export class MeshDoc {
  readonly doc: Y.Doc;
  private readonly pinsMap: Y.Map<CommunityReport>;
  private readonly routesMap: Y.Map<Route>;
  private readonly uploadedMap: Y.Map<string>;
  /** Signed reports that failed verification on receipt (kept for the UI, never merged). */
  readonly rejected: CommunityReport[] = [];

  constructor(doc = new Y.Doc()) {
    this.doc = doc;
    this.pinsMap = doc.getMap("pins");
    this.routesMap = doc.getMap("routes");
    this.uploadedMap = doc.getMap("uploaded");
  }

  // --- pins -------------------------------------------------------------------------

  /** Add a locally created, signed report. Throws if the signature does not verify. */
  addPin(report: CommunityReport): void {
    if (!verifyReport(report)) throw new Error("refusing to share an unsigned/invalid report");
    this.doc.transact(() => {
      if (!this.pinsMap.has(report.id)) this.pinsMap.set(report.id, report);
    }, "local");
  }

  pins(): CommunityReport[] {
    return [...this.pinsMap.values()].sort((a, b) => a.created_at.localeCompare(b.created_at));
  }

  pin(id: string): CommunityReport | undefined {
    return this.pinsMap.get(id);
  }

  /** Active hazard pins = hazard_pin reports (denials/all-clears are consensus input, not hazards). */
  hazardPins(): CommunityReport[] {
    return this.pins().filter((p) => p.report_type === "hazard_pin");
  }

  onPins(l: PinsListener): () => void {
    const h = () => {
      l(this.pins());
    };
    this.pinsMap.observe(h);
    return () => {
      this.pinsMap.unobserve(h);
    };
  }

  // --- routes -----------------------------------------------------------------------

  setRoute(route: Route): void {
    this.doc.transact(() => {
      const cur = this.routesMap.get(route.crowd_group_id);
      if (
        !cur ||
        route.graph_version > cur.graph_version ||
        (route.graph_version === cur.graph_version && route.version > cur.version)
      ) {
        this.routesMap.set(route.crowd_group_id, route);
      }
    }, "local");
  }

  routes(): Route[] {
    return [...this.routesMap.values()];
  }

  route(crowdGroupId: string): Route | undefined {
    return this.routesMap.get(crowdGroupId);
  }

  onRoutes(l: (routes: Route[]) => void): () => void {
    const h = () => {
      l(this.routes());
    };
    this.routesMap.observe(h);
    return () => {
      this.routesMap.unobserve(h);
    };
  }

  // --- upload bookkeeping -----------------------------------------------------------

  markUploaded(reportId: string, at: string): void {
    this.doc.transact(() => {
      if (!this.uploadedMap.has(reportId)) this.uploadedMap.set(reportId, at);
    }, "local");
  }

  isUploaded(reportId: string): boolean {
    return this.uploadedMap.has(reportId);
  }

  pendingUpload(): CommunityReport[] {
    return this.pins().filter((p) => !this.uploadedMap.has(p.id));
  }

  // --- sync primitives (used by MeshSync) --------------------------------------------

  stateVector(): Uint8Array {
    return Y.encodeStateVector(this.doc);
  }

  /** Everything the holder of `theirStateVector` is missing. */
  diffFor(theirStateVector: Uint8Array): Uint8Array {
    return Y.encodeStateAsUpdate(this.doc, theirStateVector);
  }

  fullState(): Uint8Array {
    return Y.encodeStateAsUpdate(this.doc);
  }

  /**
   * Merge a remote update. Any pin in the update that does not verify is removed again
   * (and recorded in `rejected`) so a forged report cannot spread through the mesh.
   */
  applyUpdate(update: Uint8Array, origin = "remote"): void {
    const before = new Set(this.pinsMap.keys());
    Y.applyUpdate(this.doc, update, origin);
    const bad: string[] = [];
    for (const [id, report] of this.pinsMap.entries()) {
      if (before.has(id)) continue;
      if (!verifyReport(report) || report.id !== id) bad.push(id);
    }
    if (bad.length > 0) {
      this.doc.transact(() => {
        for (const id of bad) {
          const r = this.pinsMap.get(id);
          if (r) this.rejected.push(r);
          this.pinsMap.delete(id);
        }
      }, "quarantine");
    }
  }

  /** Fires for local transactions only (what MeshSync broadcasts). */
  onLocalUpdate(l: (update: Uint8Array) => void): () => void {
    const h = (update: Uint8Array, origin: unknown) => {
      if (origin === "local") l(update);
    };
    this.doc.on("update", h);
    return () => {
      this.doc.off("update", h);
    };
  }
}
