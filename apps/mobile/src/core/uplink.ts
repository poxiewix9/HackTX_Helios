/**
 * Uplink: whenever this device has cellular / Wi-Fi reach to the blackboard, push every
 * pin in the CRDT that nobody has uploaded yet to `POST /reports`, then mark it uploaded
 * in the shared doc so the rest of the mesh stops trying. Any device with reach acts as
 * the gateway for pins that originated on isolated phones — that is the whole point.
 *
 * `fetch` is injectable; the test uses a fake. Failures are retried on the next flush.
 */
import type { CommunityReport } from "@arco/schemas";

import type { MeshDoc } from "./crdt";

export interface UplinkOptions {
  blackboardUrl: string;
  fetchFn?: typeof fetch;
  /** Scenario time to stamp uploads with (the blackboard asks for it); null = omit. */
  scenarioT?: () => number | null;
  now?: () => Date;
}

export interface UplinkResult {
  uploaded: string[];
  failed: { id: string; error: string }[];
}

export class Uplink {
  private readonly opts: Required<UplinkOptions>;
  private inflight = false;
  readonly stats = { uploaded: 0, failed: 0 };

  constructor(
    private readonly doc: MeshDoc,
    opts: UplinkOptions,
  ) {
    this.opts = {
      blackboardUrl: opts.blackboardUrl.replace(/\/$/, ""),
      fetchFn: opts.fetchFn ?? ((...a) => fetch(...a)),
      scenarioT: opts.scenarioT ?? (() => null),
      now: opts.now ?? (() => new Date()),
    };
  }

  /** Upload what is pending if `online`; no-op otherwise. Never runs concurrently. */
  async flush(online: boolean): Promise<UplinkResult> {
    const result: UplinkResult = { uploaded: [], failed: [] };
    if (!online || this.inflight) return result;
    this.inflight = true;
    try {
      for (const pin of this.doc.pendingUpload()) {
        try {
          await this.upload(pin);
          this.doc.markUploaded(pin.id, this.opts.now().toISOString());
          result.uploaded.push(pin.id);
          this.stats.uploaded += 1;
        } catch (err) {
          result.failed.push({
            id: pin.id,
            error: err instanceof Error ? err.message : String(err),
          });
          this.stats.failed += 1;
        }
      }
    } finally {
      this.inflight = false;
    }
    return result;
  }

  private async upload(pin: CommunityReport): Promise<void> {
    const t = this.opts.scenarioT();
    const url = `${this.opts.blackboardUrl}/reports${t === null ? "" : `?scenario_t=${t}`}`;
    const res = await this.opts.fetchFn(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(pin),
    });
    // 409 = the blackboard already has this report (another gateway won the race).
    if (!res.ok && res.status !== 409) {
      throw new Error(`POST /reports -> ${res.status}`);
    }
  }
}
