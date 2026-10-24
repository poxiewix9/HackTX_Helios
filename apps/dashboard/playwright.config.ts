import { defineConfig, devices } from "@playwright/test";

/**
 * Two dashboards are started:
 *  - :3200 against the real stack (compose: blackboard :8001, agents :8003, simulator :8004,
 *    benchmark :8005) for the live scenario / snap-animation test
 *  - :3201 pointed at dead ports for the "backend killed" recorded-replay test
 * Ports are deliberately not 3000 so a developer's own `pnpm dev` is left alone.
 */
const LIVE_PORT = 3200;
const DEAD_PORT = 3201;
const DEAD = "http://127.0.0.1:1"; // nothing listens here
const DEAD_WS = "ws://127.0.0.1:1";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    // Tracing reads the three WebGL canvases back on every expect poll; under headless
    // SwiftShader that stalls the page for seconds at a time and the dashboard never
    // gets to run (verified: the same spec passes with tracing off). Opt in with
    // PW_TRACE=1 when debugging a non-WebGL problem.
    trace: process.env.PW_TRACE ? "retain-on-failure" : "off",
    screenshot: "only-on-failure",
    video: "off",
    viewport: { width: 1600, height: 950 },
    launchOptions: {
      args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
    },
  },
  projects: [
    {
      name: "live",
      testMatch: /live\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${LIVE_PORT}` },
    },
    {
      name: "offline",
      testMatch: /offline\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${DEAD_PORT}` },
    },
  ],
  webServer: [
    {
      command: `pnpm exec next dev -p ${LIVE_PORT}`,
      url: `http://127.0.0.1:${LIVE_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `pnpm exec next dev -p ${DEAD_PORT}`,
      url: `http://127.0.0.1:${DEAD_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "ignore",
      stderr: "pipe",
      env: {
        NEXT_DIST_DIR: ".next-e2e-offline",
        BLACKBOARD_URL: DEAD,
        AGENTS_URL: DEAD,
        SIMULATOR_URL: DEAD,
        BENCHMARK_URL: DEAD,
        BASE_STATION_URL: DEAD,
        NEXT_PUBLIC_BLACKBOARD_WS: DEAD_WS,
        NEXT_PUBLIC_BASE_STATION_WS: DEAD_WS,
        NEXT_PUBLIC_BENCHMARK_WS: DEAD_WS,
        NEXT_PUBLIC_OFFLINE_FALLBACK_MS: "2500",
      },
    },
  ],
});
