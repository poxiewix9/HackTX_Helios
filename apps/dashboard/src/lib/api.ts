/**
 * Same-origin REST client (proxied by Next rewrites). Response types come from
 * `@arco/schemas`; simulator control responses that are not part of the message contract
 * are typed as `Pick<Scenario, ...>` (derived, never hand-duplicated).
 */
import type {
  AgentEvent,
  CommunityReport,
  Hazard,
  HazardClass,
  HazardGeometry,
  RoadEdge,
  Route,
  SafeZone,
  Scenario,
} from "@arco/schemas";

import { config } from "@/lib/config";

export type ScenarioSummary = Pick<
  Scenario,
  "id" | "name" | "event" | "event_date" | "location" | "duration_s" | "label"
>;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(url: string, init?: RequestInit, timeoutMs = 8000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => {
    ctrl.abort();
  }, timeoutMs);
  try {
    const res = await fetch(url, {
      ...init,
      signal: ctrl.signal,
      headers: {
        "content-type": "application/json",
        ...(init?.headers as Record<string, string> | undefined),
      },
    });
    if (!res.ok) throw new ApiError(res.status, `${init?.method ?? "GET"} ${url} -> ${res.status}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

const bb = config.rest.bb;
const sim = config.rest.sim;

export const api = {
  blackboard: {
    health: () => request<{ status: string }>(`${bb}/health`, undefined, 3000),
    edges: (bbox: [number, number, number, number]) =>
      request<RoadEdge[]>(`${bb}/edges?bbox=${bbox.join(",")}`, undefined, 20000),
    hazards: () => request<Hazard[]>(`${bb}/hazards`),
    routes: () => request<Route[]>(`${bb}/routes`),
    safeZones: () => request<SafeZone[]>(`${bb}/safe-zones`),
    reports: () => request<CommunityReport[]>(`${bb}/reports`),
    agentEvents: (limit = 200) => request<AgentEvent[]>(`${bb}/agent-events?limit=${limit}`),
  },
  sim: {
    scenarios: () => request<ScenarioSummary[]>(`${sim}/scenarios`),
    scenario: () => request<Scenario>(`${sim}/scenario`),
    load: (scenario_id: string) =>
      request<unknown>(
        `${sim}/load`,
        { method: "POST", body: JSON.stringify({ scenario_id }) },
        60000,
      ),
    play: () => request<unknown>(`${sim}/play`, { method: "POST" }),
    pause: () => request<unknown>(`${sim}/pause`, { method: "POST" }),
    speed: (speed: number) =>
      request<unknown>(`${sim}/speed`, { method: "POST", body: JSON.stringify({ speed }) }),
    seek: (t: number) =>
      request<unknown>(`${sim}/seek`, { method: "POST", body: JSON.stringify({ t }) }, 60000),
    chaosHazard: (body: {
      geom: HazardGeometry;
      hazard_class: HazardClass;
      confidence?: number;
      velocity_m_s?: number | null;
      note?: string | null;
    }) => request<unknown>(`${sim}/chaos/hazard`, { method: "POST", body: JSON.stringify(body) }),
    chaosCellular: (up: boolean, reason?: string) =>
      request<unknown>(`${sim}/chaos/cellular`, {
        method: "POST",
        body: JSON.stringify({ up, reason: reason ?? null }),
      }),
    frameUrl: (cameraId: string, t: number) =>
      `${sim}/feeds/${encodeURIComponent(cameraId)}/frame.jpg?t=${t.toFixed(1)}`,
  },
};
