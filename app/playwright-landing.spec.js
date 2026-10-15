const { test, expect } = require("@playwright/test");

test("landing navigation, login, session restoration and logout", async ({ page }) => {
  let authenticated = false;
  await page.route("**/auth/status", route => route.fulfill({ json: { authenticated } }));
  await page.route("**/auth/login", async route => {
    authenticated = route.request().postDataJSON().password === "test-password";
    await route.fulfill({ status: authenticated ? 200 : 401, json: authenticated ? { authenticated } : { detail: "Invalid password" } });
  });
  await page.route("**/auth/logout", route => {
    authenticated = false;
    return route.fulfill({ json: { authenticated } });
  });
  // Keep this UI test independent of market providers and a configured database.
  await page.route("**/phase2/**", route => route.fulfill({ status: 503, json: { detail: "Test data unavailable" } }));
  await page.route("**/health", route => route.fulfill({ json: { status: "ok", fmp_configured: false } }));
  await page.goto("/");
  await expect(page.locator("#landingScreen")).toBeVisible();
  await expect(page.locator("#loginScreen")).toBeHidden();
  await page.getByRole("link", { name: "Help", exact: true }).click();
  await page.getByText("How do I access the dashboard?").click();
  await expect(page.getByText("enter your dashboard password", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Contact Us", exact: true }).click();
  await expect(page.getByRole("link", { name: "Contact the team" })).toHaveAttribute("href", "https://github.com/phuc-minu/momentum-tracker/issues");
  await page.getByRole("navigation", { name: "Main menu" }).getByRole("link", { name: "Sign in" }).click();
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Login", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Invalid password");
  await page.getByLabel("Password").fill("test-password");
  await page.getByRole("button", { name: "Login", exact: true }).click();
  await expect(page.locator("#dashboard")).toBeVisible();
  await expect(page.locator("#landingScreen")).toBeHidden();
  await page.reload();
  await expect(page.locator("#dashboard")).toBeVisible();
  await page.getByRole("button", { name: "Logout" }).click();
  await expect(page.locator("#loginScreen")).toBeVisible();
  await page.getByRole("link", { name: "Back to home" }).click();
  await expect(page.locator("#landingScreen")).toBeVisible();
  await page.goto("/#login");
  await expect(page.locator("#loginScreen")).toBeVisible();
});

for (const width of [375, 768, 1440]) {
  test(`landing fits a ${width}px viewport`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Find the signal." })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main menu" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole("link", { name: "Open your dashboard" }).click();
    await expect(page.getByLabel("Password")).toBeVisible();
    await page.getByRole("link", { name: "Back to home" }).click();
    await expect(page.locator("#landingScreen")).toBeVisible();
  });
}
