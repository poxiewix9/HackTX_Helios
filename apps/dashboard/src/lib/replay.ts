/**
 * Recorded replay: the offline fallback when the live stack is unreachable.
 *
 * A replay file is a capture of real bus messages (recorded by `scripts/record-replay.mjs`
 * from a running stack) plus the REST snapshot needed to draw the map. The player emits
 * the messages in `scenario_t` order on its own wall clock — the only situation in which
 * the dashboard runs a clock of its own, and it is labelled as RECORDED REPLAY.
 */
import type { AnyMessage, RoadEdge, SafeZone, Scenario } from "@arco/schemas";

export interface ReplayFile {
  format: "arco.replay/1";
  recorded_at: string;
  label: string;
  notes?: string;
  scenario: Scenario;
  edges: RoadEdge[];
  safe_zones: SafeZone[];
  messages: AnyMessage[];
  telemetry: AnyMessage[];
}

export function isReplayFile(x: unknown): x is ReplayFile {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Partial<ReplayFile>;
  return (
    r.format === "arco.replay/1" && Array.isArray(r.messages) && typeof r.scenario === "object"
  );
}

export interface ReplayPlayerOptions {
  messages: AnyMessage[];
  emit: (msg: AnyMessage) => void;
  /** scenario seconds per wall second */
  speed?: number;
  loop?: boolean;
  now?: () => number;
  schedule?: (fn: () => void, ms: number) => unknown;
  cancel?: (handle: unknown) => void;
}

/** Messages sorted by scenario_t, stable by seq, with timeline/clock messages first at equal t. */
export function orderForReplay(messages: AnyMessage[]): AnyMessage[] {
  return messages
    .map((m, i) => ({ m, i }))
    .sort((a, b) => a.m.scenario_t - b.m.scenario_t || a.m.seq - b.m.seq || a.i - b.i)
    .map((x) => x.m);
}

export class ReplayPlayer {
  private readonly messages: AnyMessage[];
  private readonly emit: (msg: AnyMessage) => void;
  private readonly speed: number;
  private readonly loop: boolean;
  private readonly now: () => number;
  private readonly schedule: (fn: () => void, ms: number) => unknown;
  private readonly cancel: (handle: unknown) => void;
  private cursor = 0;
  private startWall = 0;
  private startT = 0;
  private handle: unknown = null;
  running = false;

  constructor(opts: ReplayPlayerOptions) {
    this.messages = orderForReplay(opts.messages);
    this.emit = opts.emit;
    this.speed = opts.speed ?? 20;
    this.loop = opts.loop ?? true;
    this.now = opts.now ?? (() => performance.now());
    this.schedule = opts.schedule ?? ((fn, ms) => setTimeout(fn, ms));
    this.cancel =
      opts.cancel ??
      ((h) => {
        clearTimeout(h as number);
      });
  }

  get length(): number {
    return this.messages.length;
  }

  get durationS(): number {
    const last = this.messages[this.messages.length - 1];
    return last ? last.scenario_t : 0;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.cursor = 0;
    this.startWall = this.now();
    this.startT = this.messages[0]?.scenario_t ?? 0;
    this.pump();
  }

  stop(): void {
    this.running = false;
    if (this.handle !== null) {
      this.cancel(this.handle);
      this.handle = null;
    }
  }

  /** Scenario time the replay is currently at. */
  currentT(): number {
    if (!this.running) return this.startT;
    return this.startT + ((this.now() - this.startWall) / 1000) * this.speed;
  }

  /** Emit everything due now; schedule the next wake-up. Returns the number emitted. */
  pump(): number {
    if (!this.running) return 0;
    this.handle = null;
    const t = this.currentT();
    let emitted = 0;
    while (this.cursor < this.messages.length) {
      const m = this.messages[this.cursor];
      if (!m || m.scenario_t > t) break;
      this.cursor += 1;
      emitted += 1;
      try {
        this.emit(m);
      } catch (err) {
        console.error("replay emit failed", err);
      }
    }
    if (this.cursor >= this.messages.length) {
      if (this.loop && this.messages.length > 0) {
        this.cursor = 0;
        this.startWall = this.now() + 2000; // 2 s hold at the end before looping
        this.handle = this.schedule(() => this.pump(), 2000);
      } else {
        this.running = false;
      }
      return emitted;
    }
    const next = this.messages[this.cursor];
    const waitMs = next ? Math.max(0, ((next.scenario_t - t) / this.speed) * 1000) : 0;
    this.handle = this.schedule(() => this.pump(), Math.min(waitMs, 500));
    return emitted;
  }
}

export async function fetchReplay(url: string): Promise<ReplayFile> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`replay ${url} -> ${res.status}`);
  const data: unknown = await res.json();
  if (!isReplayFile(data)) throw new Error(`replay ${url}: not an arco.replay/1 file`);
  return data;
}
