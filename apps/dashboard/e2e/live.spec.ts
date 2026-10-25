/**
 * Live-stack smoke test (needs `docker compose up -d --wait` with blackboard, agents,
 * simulator and benchmark). Loads the dashboard, plays the default scenario through the
 * simulator's control bar, and asserts that the route "snap" fires after a hazard event.
 * Also records the measured three.js frame rate and the telemetry LIVE/RECORDED badge.
 */
import { expect, test } from "@playwright/test";

const SIM = process.env.SIMULATOR_URL ?? "http://localhost:8004";

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${SIM}/health`).catch(() => null);
  test.skip(!health?.ok(), "simulator (:8004) not reachable — start the compose stack");
  if (!health) return; // unreachable after the skip above; keeps the type narrow
  // Known starting point: default scenario at t=0, paused.
  const h = (await health.json()) as { scenario_id: string | null };
  if (!h.scenario_id) await request.post(`${SIM}/load`, { data: { scenario_id: "lahaina_2023" } });
  await request.post(`${SIM}/pause`);
  await request.post(`${SIM}/seek`, { data: { t: 0 } });
});

test("plays the default scenario and the snap animation fires after a hazard event", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("mode-badge")).toHaveText("LIVE STACK", { timeout: 30_000 });
  await expect(page.getByTestId("conn-blackboard")).toContainText("blackboard");
  await expect(page.locator("[data-testid=conn-blackboard].text-ops-ok")).toBeVisible();

  // The honesty label is on screen and the scenario loaded.
  await expect(page.getByText("Simulated on real event geometry").first()).toBeVisible();
  await expect(page.getByTestId("scenario-select")).toBeEnabled();

  const snap = page.getByTestId("snap-indicator");
  const before = Number((await snap.getAttribute("data-count")) ?? "0");

  await page.getByTestId("speed-select").selectOption("200");
  await page.getByTestId("play-button").click();
  await expect(page.getByTestId("pause-button")).toBeVisible();

  // A hazard event reaches the dashboard (fire-east at t≈40 s scenario time)...
  await expect
    .poll(async () => page.locator("[data-testid=agent-event]").count(), { timeout: 60_000 })
    .toBeGreaterThan(0);
  await expect(
    page.locator("[data-testid=agent-event]").filter({ hasText: "hazard.applied" }).first(),
  ).toBeVisible({
    timeout: 60_000,
  });

  // ...and the snap fires: count increases and the indicator passes through firing -> fired.
  await expect
    .poll(async () => Number((await snap.getAttribute("data-count")) ?? "0"), { timeout: 60_000 })
    .toBeGreaterThan(before);
  await expect(snap).toHaveAttribute("data-state", /firing|fired/);

  // The scenario clock on screen is the backend's clock (it advanced past 0).
  await expect
    .poll(async () => (await page.getByTestId("clock-t").textContent()) ?? "00:00")
    .not.toBe("00:00");

  // Pause the backend clock (every re-route at 200x re-arms the flash, so the indicator
  // only settles once routes stop changing) and watch the animation finish: firing -> fired.
  const pause = page.getByTestId("pause-button");
  if (await pause.isVisible()) await pause.click();
  await expect(page.getByTestId("play-button")).toBeVisible({ timeout: 30_000 });
  await expect(snap).toHaveAttribute("data-state", "fired", { timeout: 15_000 });
  const snapText = (await snap.textContent()) ?? "";
  test.info().annotations.push({ type: "snap", description: snapText.trim() });

  // Telemetry badge reports LIVE or RECORDED from the sample itself.
  await expect(page.getByTestId("telemetry-mode")).toHaveText(/LIVE|RECORDED/, { timeout: 30_000 });
  test.info().annotations.push({
    type: "telemetry",
    description: (await page.getByTestId("telemetry-mode").textContent()) ?? "",
  });

  // Three.js layer: report the measured FPS (headless SwiftShader numbers are not the laptop's).
  const fpsEl = page.getByTestId("three-fps");
  await expect
    .poll(async () => (await fpsEl.getAttribute("data-fps")) ?? "", { timeout: 30_000 })
    .not.toBe("");
  const fps = Number(await fpsEl.getAttribute("data-fps"));
  expect(fps).toBeGreaterThan(0);
  test.info().annotations.push({
    type: "three-fps",
    description: `${fps.toFixed(1)} fps (${(await fpsEl.getAttribute("title")) ?? ""})`,
  });

  // Street camera plays local demo footage (video), not the simulator JPEG.
  await expect(page.getByTestId("street-feed").locator("video")).toBeVisible({ timeout: 30_000 });

  // Still paused: the clock did not drift on its own while we were looking around.
  await expect(page.getByTestId("play-button")).toBeVisible();
});

test("mesh panel: a pin dropped on an isolated phone reaches the gateway over the mesh and uploads when cellular returns", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("mode-badge")).toHaveText("LIVE STACK", { timeout: 30_000 });
  const stats = page.getByTestId("mesh-stats");
  await expect(stats).toBeVisible({ timeout: 30_000 });
  const gateway = page.getByTestId("mesh-device-gateway");
  const phone5 = page.getByTestId("mesh-device-phone-5");
  await expect(gateway).toBeVisible({ timeout: 30_000 });

  // Cellular off for every simulated phone: the gateway cannot upload, only mesh moves data.
  const cell = page.getByTestId("mesh-cellular");
  if ((await cell.getAttribute("data-state")) === "on") await cell.click();
  await expect(cell).toHaveAttribute("data-state", "off");
  await expect(phone5).toHaveAttribute("data-conn", /mesh|isolated/);

  const gwBefore = Number((await gateway.getAttribute("data-pins")) ?? "0");
  const upBefore = Number((await stats.getAttribute("data-uploaded")) ?? "0");

  // phone-5 is the default selection (end of the bridged chain, 3 hops from the gateway).
  await page.getByTestId("mesh-drop-pin").click();
  await page.getByTestId("mesh-pin-blocked_road").click();
  await expect(phone5).toHaveAttribute("data-pins", String(gwBefore + 1), { timeout: 10_000 });
  // Multi-hop CRDT propagation with cellular down.
  await expect(gateway).toHaveAttribute("data-pins", String(gwBefore + 1), { timeout: 15_000 });
  // ...but nothing was uploaded while cellular was off.
  expect(Number((await stats.getAttribute("data-uploaded")) ?? "0")).toBe(upBefore);

  // Cellular back: the gateway uploads the pending pin exactly once and the consensus
  // agent reports it in the agent feed.
  await cell.click();
  await expect(cell).toHaveAttribute("data-state", "on");
  await expect
    .poll(async () => Number((await stats.getAttribute("data-uploaded")) ?? "0"), {
      timeout: 15_000,
    })
    .toBe(upBefore + 1);
  await expect(
    page
      .locator("[data-testid=agent-event]")
      .filter({ hasText: /report\./ })
      .first(),
  ).toBeVisible({ timeout: 30_000 });
});

test("chaos button: cellular kill is reflected from the network event", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(page.getByTestId("mode-badge")).toHaveText("LIVE STACK", { timeout: 30_000 });
  await request.post(`${SIM}/chaos/cellular`, { data: { up: true, reason: "e2e reset" } });
  await expect(page.getByTestId("cellular-badge")).toHaveText(/UP/, { timeout: 15_000 });
  await page.getByTestId("chaos-button").click();
  await page.getByTestId("chaos-cellular").click();
  await expect(page.getByTestId("cellular-badge")).toHaveText(/DOWN/, { timeout: 15_000 });
  await request.post(`${SIM}/chaos/cellular`, { data: { up: true, reason: "e2e restore" } });
  await expect(page.getByTestId("cellular-badge")).toHaveText(/UP/, { timeout: 15_000 });
});
