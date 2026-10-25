/**
 * MeshNode — one device's whole offline-first stack in a box: identity key, CRDT doc,
 * sync over any number of transports, uplink when it has reach, fallback router.
 * Used by the Expo app (one node = this phone) and by the dashboard's mesh panel
 * (one node per simulated device).
 */
import type { CommunityReport, HazardClass, Route } from "@arco/schemas";

import { MeshDoc } from "./crdt";
import type { LngLat } from "./geo";
import { routeToSafety } from "./router";
import type { GraphTile, RouteResult } from "./router";
import { DeviceKey } from "./signing";
import { MeshSync } from "./sync";
import type { MeshSyncOptions } from "./sync";
import { connectivityOf } from "./transport";
import type { Connectivity, Transport } from "./transport";
import { Uplink } from "./uplink";
import type { UplinkOptions } from "./uplink";

export interface MeshNodeOptions {
  id: string;
  key?: DeviceKey;
  transports?: Transport[];
  sync?: Partial<MeshSyncOptions>;
  uplink?: UplinkOptions;
  tile?: GraphTile | null;
}

export interface ActiveRoute {
  source: "navigator" | "on-device-fallback";
  path: LngLat[];
  lengthM: number;
  instruction: string;
  safeZoneId: string;
  graphVersion: number | null;
}

export class MeshNode {
  readonly id: string;
  readonly key: DeviceKey;
  readonly doc: MeshDoc;
  readonly sync: MeshSync;
  readonly uplink: Uplink | null;
  tile: GraphTile | null;
  /** Set by the host (app: network reachability; panel: the simulated backhaul). */
  cellularUp = false;
  /** The crowd group this device belongs to (from the nearest origin cell); null = unknown. */
  crowdGroupId: string | null = null;
  position: LngLat | null = null;

  constructor(opts: MeshNodeOptions) {
    this.id = opts.id;
    this.key = opts.key ?? DeviceKey.fromSeed(`arco-mobile:${opts.id}`);
    this.doc = new MeshDoc();
    this.sync = new MeshSync(this.doc, opts.sync);
    for (const t of opts.transports ?? []) this.sync.addTransport(t);
    this.uplink = opts.uplink ? new Uplink(this.doc, opts.uplink) : null;
    this.tile = opts.tile ?? null;
  }

  start(): void {
    this.sync.start();
  }

  stop(): void {
    this.sync.stop();
  }

  connectivity(): Connectivity {
    return connectivityOf(this.cellularUp, this.sync.peers().length);
  }

  /** Sign and share a hazard pin at `at` (defaults to the device position). */
  dropPin(
    hazardClass: HazardClass,
    note: string | null = null,
    at: LngLat | null = this.position,
  ): CommunityReport {
    if (!at) throw new Error("no position for the pin");
    const report = this.key.makeReport({
      lon: at[0],
      lat: at[1],
      report_type: "hazard_pin",
      hazard_class: hazardClass,
      note,
    });
    this.doc.addPin(report);
    return report;
  }

  /** Deny / all-clear an existing hazard (consensus input, not a hazard). */
  denyHazard(
    targetHazardId: string,
    at: LngLat | null = this.position,
    note: string | null = null,
  ): CommunityReport {
    if (!at) throw new Error("no position for the report");
    const report = this.key.makeReport({
      lon: at[0],
      lat: at[1],
      report_type: "all_clear",
      target_hazard_id: targetHazardId,
      note,
    });
    this.doc.addPin(report);
    return report;
  }

  /** Called by whoever hears the agents (`routes.updated`): share the route over the mesh. */
  ingestRoutes(routes: Route[]): void {
    for (const r of routes) this.doc.setRoute(r);
  }

  /**
   * The route to follow: the Navigator's route for my crowd group if the mesh has one,
   * otherwise the on-device A* on the tile around the pinned hazards.
   */
  activeRoute(): ActiveRoute | null {
    if (this.crowdGroupId) {
      const r = this.doc.route(this.crowdGroupId);
      if (r) {
        return {
          source: "navigator",
          path: r.path.coordinates.map((c) => [c[0], c[1]] as LngLat),
          lengthM: r.length_m,
          instruction: r.instruction,
          safeZoneId: r.safe_zone_id,
          graphVersion: r.graph_version,
        };
      }
    }
    if (this.tile && this.position) {
      const fb: RouteResult | null = routeToSafety(this.tile, this.position, this.doc.hazardPins());
      if (fb) {
        return {
          source: "on-device-fallback",
          path: fb.path,
          lengthM: fb.lengthM,
          instruction: `On-device route to ${fb.safeZoneId} (${Math.round(fb.lengthM)} m) — not the Navigator`,
          safeZoneId: fb.safeZoneId,
          graphVersion: null,
        };
      }
    }
    return null;
  }

  async flushUplink(): Promise<number> {
    if (!this.uplink) return 0;
    const r = await this.uplink.flush(this.cellularUp);
    return r.uploaded.length;
  }
}
