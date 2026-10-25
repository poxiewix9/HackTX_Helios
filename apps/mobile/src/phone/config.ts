/**
 * App configuration. `EXPO_PUBLIC_*` variables are inlined by Metro at build time; set
 * them in `apps/mobile/.env` or the shell (see README). Defaults point at a laptop on
 * the same Wi-Fi only if you replace `localhost` with its LAN address.
 */
function pick(v: unknown, fallback: string): string {
  return typeof v === "string" && v !== "" ? v : fallback;
}

export const appConfig = {
  /** Dev relay (`pnpm --filter @arco/mobile relay`). */
  meshRelayUrl: pick(process.env.EXPO_PUBLIC_MESH_RELAY_URL, "ws://localhost:8787"),
  /** Blackboard REST, used only while this device has (simulated) cellular. */
  blackboardUrl: pick(process.env.EXPO_PUBLIC_BLACKBOARD_URL, "http://localhost:8001"),
  scenarioId: pick(process.env.EXPO_PUBLIC_DEFAULT_SCENARIO, "lahaina_2023"),
  /** Mesh room = scenario id, so phones and the dashboard panel meet in the same room. */
  get room(): string {
    return `arco:${this.scenarioId}`;
  },
  /** Simulated walking speed along the route (m/s). */
  walkSpeedMps: 1.4,
  uplinkEveryMs: 2000,
  routesPollEveryMs: 3000,
};
