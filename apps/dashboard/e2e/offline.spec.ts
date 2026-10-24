/**
 * Backend-killed test: this project's dev server points every REST rewrite and WebSocket
 * at a port where nothing listens. The dashboard must fall back to the recorded replay,
 * say so, and still animate routes / hazards / agent reasoning from the recording.
 */
import { expect, test } from "@playwright/test";

test("falls back to the recorded replay when the backend is unreachable", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  await page.goto("/");
  // Sockets keep retrying with backoff and say so.
  await expect(page.getByTestId("conn-blackboard")).toContainText(/blackboard/);
  await expect(page.getByTestId("mode-badge")).toHaveText("RECORDED REPLAY", { timeout: 30_000 });
  await expect(page.getByTestId("replay-banner")).toContainText("backend unreachable");
  await expect(page.getByText("Simulated on real event geometry").first()).toBeVisible();

  // Recorded messages drive the same reducer: agent reasoning and snaps appear.
  await expect
    .poll(async () => page.locator("[data-testid=agent-event]").count(), { timeout: 60_000 })
    .toBeGreaterThan(0);
  const snap = page.getByTestId("snap-indicator");
  await expect
    .poll(async () => Number((await snap.getAttribute("data-count")) ?? "0"), { timeout: 60_000 })
    .toBeGreaterThan(0);

  // The telemetry strip is labelled RECORDED, never LIVE, in this mode.
  await expect(page.getByTestId("telemetry-mode")).toHaveText("RECORDED", { timeout: 30_000 });

  // Transport controls are disabled: there is no backend clock to drive (the recorded
  // clock ticks may say "playing", so the button can read play or pause).
  await expect(
    page.locator("[data-testid=play-button], [data-testid=pause-button]"),
  ).toBeDisabled();
  await expect(page.getByTestId("scrub")).toBeDisabled();
  await expect(page.getByTestId("chaos-button")).toBeDisabled();

  // Reconnect attempts are visible (attempt counter climbs).
  await expect
    .poll(async () => (await page.getByTestId("conn-blackboard").textContent()) ?? "", {
      timeout: 30_000,
    })
    .toMatch(/#\d+/);

  // No uncaught exceptions while running on the recording.
  expect(pageErrors).toEqual([]);
});
