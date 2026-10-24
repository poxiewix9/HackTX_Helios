/**
 * Reconnecting WebSocket with exponential backoff + jitter. Surfaces its state so the
 * UI can show it; never throws out of callbacks. Injectable WebSocket factory and clock
 * so the behaviour is unit-tested without a network.
 */
export type ConnState = "connecting" | "open" | "reconnecting" | "closed";

export interface ReconnectingSocketOptions {
  url: string;
  onMessage: (data: string) => void;
  onState?: (state: ConnState, attempt: number) => void;
  /** First retry delay (ms). Doubles each attempt up to `maxDelayMs`. */
  baseDelayMs?: number;
  maxDelayMs?: number;
  /** 0..1 fraction of the delay added as random jitter. */
  jitter?: number;
  factory?: (url: string) => WebSocketLike;
  random?: () => number;
  setTimeoutFn?: (fn: () => void, ms: number) => unknown;
  clearTimeoutFn?: (handle: unknown) => void;
}

/** The subset of the WebSocket API we use (so tests can fake it). */
export interface WebSocketLike {
  onopen: ((ev: unknown) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  close(): void;
  readyState: number;
}

export function backoffDelay(
  attempt: number,
  baseDelayMs: number,
  maxDelayMs: number,
  jitter: number,
  random: () => number,
): number {
  const exp = Math.min(maxDelayMs, baseDelayMs * 2 ** Math.max(0, attempt - 1));
  return Math.round(exp + exp * jitter * random());
}

export class ReconnectingSocket {
  readonly url: string;
  state: ConnState = "closed";
  attempt = 0;
  private ws: WebSocketLike | null = null;
  private timer: unknown = null;
  private stopped = false;
  private readonly opts: Required<
    Omit<ReconnectingSocketOptions, "onState" | "url" | "onMessage">
  > & { onState: ((state: ConnState, attempt: number) => void) | undefined };
  private readonly onMessage: (data: string) => void;

  constructor(options: ReconnectingSocketOptions) {
    this.url = options.url;
    this.onMessage = options.onMessage;
    this.opts = {
      onState: options.onState,
      baseDelayMs: options.baseDelayMs ?? 500,
      maxDelayMs: options.maxDelayMs ?? 10_000,
      jitter: options.jitter ?? 0.3,
      factory: options.factory ?? ((url) => new WebSocket(url) as unknown as WebSocketLike),
      random: options.random ?? Math.random,
      setTimeoutFn: options.setTimeoutFn ?? ((fn, ms) => setTimeout(fn, ms)),
      clearTimeoutFn:
        options.clearTimeoutFn ??
        ((h) => {
          clearTimeout(h as number);
        }),
    };
  }

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) {
      this.opts.clearTimeoutFn(this.timer);
      this.timer = null;
    }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onopen = ws.onclose = ws.onerror = ws.onmessage = null;
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    }
    this.setState("closed");
  }

  private setState(state: ConnState): void {
    this.state = state;
    this.opts.onState?.(state, this.attempt);
  }

  private connect(): void {
    if (this.stopped) return;
    this.attempt += 1;
    this.setState(this.attempt === 1 ? "connecting" : "reconnecting");
    let ws: WebSocketLike;
    try {
      ws = this.opts.factory(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.setState("open");
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === "string") {
        try {
          this.onMessage(ev.data);
        } catch (err) {
          console.error("ws message handler failed", err);
        }
      }
    };
    ws.onerror = () => {
      /* onclose always follows */
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const delay = backoffDelay(
      Math.max(1, this.attempt),
      this.opts.baseDelayMs,
      this.opts.maxDelayMs,
      this.opts.jitter,
      this.opts.random,
    );
    this.setState("reconnecting");
    this.timer = this.opts.setTimeoutFn(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }
}
