const { test, expect } = require("@playwright/test");

const baseUrl = process.env.SITE_URL || "https://momentumai-api.bluepebble-c6bb0fe0.westus2.azurecontainerapps.io/";

test("authenticated dashboard smoke test", async ({ page }) => {
  const password = process.env.APP_PASSWORD;
  expect(password, "APP_PASSWORD must be set in the environment").toBeTruthy();

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Login" }).click();
  await expect(page.locator("#dashboard")).toBeVisible({ timeout: 30000 });

  await expect(page.getByRole("button", { name: "Discovery" })).toHaveClass(/active/);
  await expect(page.locator("#discoveryPanel")).toHaveClass(/active/);
  await expect(page.getByRole("heading", { name: "PostgreSQL Momentum Candidates" })).toBeVisible();
  await expect(page.locator("#discoverMeta")).not.toHaveText("Loading candidates", { timeout: 30000 });
  await expect(page.locator("#discoveryPanel")).toContainText("Symbol");
  await expect(page.locator("#discoveryPanel")).toContainText("Score");
  await expect(page.locator("#discoveryPanel")).toContainText("Options");

  await page.getByRole("button", { name: "Overextended" }).click();
  await expect(page.getByRole("button", { name: "Overextended" })).toHaveClass(/active/);
  await expect(page.locator("#overextendedPanel")).toHaveClass(/active/);
  await expect(page.getByRole("heading", { name: "Pullback Watch" })).toBeVisible();
  await expect(page.locator("#overextendedMeta")).not.toHaveText("Loading pullback watch", { timeout: 30000 });
  await expect(page.locator("#overextendedPanel")).toContainText("Risk");
  await expect(page.locator("#overextendedPanel")).toContainText("RSI");
  await expect(page.locator("#overextendedPanel")).toContainText("Very high");

  await page.getByRole("button", { name: "Biotech Catalysts" }).click();
  await expect(page.getByRole("button", { name: "Biotech Catalysts" })).toHaveClass(/active/);
  await expect(page.locator("#biotechPanel")).toHaveClass(/active/);
  await expect(page.getByRole("heading", { name: "Future Event Tracker" })).toBeVisible();
  await expect(page.locator("#biotechMeta")).toContainText("catalysts");
  await expect(page.locator("#biotechPanel")).toContainText("Event");
  await expect(page.locator("#biotechPanel")).toContainText("Impact");

  await page.getByRole("button", { name: "Discovery" }).click();
  await expect(page.getByRole("button", { name: "Discovery" })).toHaveClass(/active/);
  await expect(page.locator("#discoveryPanel")).toHaveClass(/active/);

  const health = await page.evaluate(async () => {
    const response = await fetch("/health");
    return response.json();
  });
  expect(health).toMatchObject({
    status: "ok",
    provider: "fmp",
    marketdata_configured: true,
  });

  await page.screenshot({ path: ".local/playwright-auth-smoke.png", fullPage: true });
});
