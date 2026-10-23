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
test("Discover accordion details are accessible, sortable, and responsive", async ({ page, context }) => {
  await page.route("**/auth/status", route => route.fulfill({ json: { authenticated: true } }));
  await page.route("**/health", route => route.fulfill({ json: { status: "ok", fmp_configured: false } }));
  await page.route("**/phase2/**", route => route.fulfill({ json: { results: [] } }));
  await context.route("https://finance.yahoo.com/**", route => route.fulfill({ status: 200, contentType: "text/html", body: "<title>Yahoo stub</title>" }));
  const results = [
    { symbol: "ZZZ", sector: "Technology", score: 95, setup: "Breakout", catalyst: "Product launch", last_close: 20, factors: { return_1m: 0.12, return_2w: 0.05, rsi_14: 61, distance_to_50d_sma: 0.03 } },
    { symbol: "AAA", sector: "Healthcare", score: 80, setup: "Reversal", last_close: 10, factors: { return_1m: 0.08, return_2w: 0.04, rsi_14: 55 } },
    { symbol: "MMM", sector: "Energy", score: 70, last_close: 8, factors: { return_1m: 0.06 } },
  ];
  await page.route("**/phase2/candidates?*", route => route.fulfill({ json: { results, growth_min: 0, period_days: 30, limit: 10 } }));
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/");
  const toggles = page.locator("#discoverBody .row-toggle");
  await expect(toggles).toHaveCount(3);
  for (const symbol of ["ZZZ", "AAA", "MMM"]) {
    const toggle = page.getByRole("button", { name: `Show details for ${symbol}` });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const detailsId = await toggle.getAttribute("aria-controls");
    await expect(page.locator(`#${detailsId}.row-details`)).toBeHidden();
  }
  const zzzToggle = page.locator('.row-toggle[data-symbol="ZZZ"]');
  await zzzToggle.click();
  await expect(zzzToggle).toHaveAttribute("aria-expanded", "true");
  const zzzPanel = page.locator(`#${await zzzToggle.getAttribute("aria-controls")}`);
  await expect(zzzPanel).toBeVisible();
  await expect(zzzPanel).toContainText("Score breakdown");
  await expect(zzzPanel).toContainText("Breakout");
  await expect(zzzPanel).toContainText("1M return");
  await zzzToggle.click();
  await expect(zzzPanel).toBeHidden();
  await zzzToggle.press("Enter");
  await expect(zzzToggle).toHaveAttribute("aria-expanded", "true");
  await zzzToggle.press(" ");
  await expect(zzzToggle).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "Show details for ZZZ" }).click();
  await page.getByRole("button", { name: "Show details for AAA" }).click();
  await expect(page.getByRole("button", { name: "Hide details for ZZZ" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Hide details for AAA" })).toHaveCount(1);
  const missingToggle = page.locator('.row-toggle[data-symbol="MMM"]');
  await missingToggle.click();
  await expect(page.locator(`#${await missingToggle.getAttribute("aria-controls")}`)).toContainText("Setup: —");
  const symbolLink = page.locator(".symbol-link").filter({ hasText: "ZZZ" });
  await expect(symbolLink).toHaveAttribute("href", "https://finance.yahoo.com/quote/ZZZ");
  await expect(symbolLink).toHaveAttribute("target", "_blank");
  const [popup] = await Promise.all([page.waitForEvent("popup"), symbolLink.click()]);
  await popup.close();
  await expect(page.getByRole("button", { name: "Hide details for ZZZ" })).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "Symbol", exact: true }).click();
  await expect(page.locator("#discoverBody .symbol-link")).toHaveText(["AAA", "MMM", "ZZZ"]);
  for (const symbol of ["ZZZ", "AAA", "MMM"]) {
    const toggle = page.getByRole("button", { name: new RegExp(`details for ${symbol}$`) });
    const detailsId = await toggle.getAttribute("aria-controls");
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.locator(`#${detailsId}`)).toBeVisible();
    await expect(toggle.locator("xpath=../..").locator("xpath=following-sibling::tr[1]")).toHaveAttribute("id", detailsId);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
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

test("Sector / Exchange sorts displayed labels and preserves score ties", async ({ page }) => {
  await page.route("**/auth/status", route => route.fulfill({ json: { authenticated: true } }));
  await page.route("**/health", route => route.fulfill({ json: { status: "ok", fmp_configured: false } }));
  await page.route("**/phase2/**", route => route.fulfill({ json: { results: [] } }));
  const results = [
    { symbol: "TECH", sector: "Technology", score: 95 },
    { symbol: "NYSE", sector: "Unclassified", exchange: "N", score: 90 },
    { symbol: "LOW", sector: "healthcare", score: 70 },
    { symbol: "HIGH", sector: "Healthcare", score: 80 },
    { symbol: "NAS", sector: null, exchange: "NASDAQ", score: 92 },
  ].map(item => ({ ...item, last_close: 20, factors: { return_1m: 0.5 } }));
  await page.route("**/phase2/candidates?*", route => route.fulfill({ json: { results } }));
  await page.goto("/");
  const symbols = page.locator("#discoverBody .symbol-link");
  // Keep default score order distinct from descending sector/exchange order.
  await expect(symbols).toHaveText(["TECH", "NAS", "NYSE", "HIGH", "LOW"]);
  const sort = page.getByRole("button", { name: "Sector / Exchange", exact: true });
  const header = sort.locator("..");
  await sort.click();
  await expect(symbols).toHaveText(["HIGH", "LOW", "NAS", "NYSE", "TECH"]);
  await expect(header).toHaveAttribute("aria-sort", "ascending");
  await expect(sort).toHaveClass(/active/);
  await expect(sort.locator(".sort-indicator")).toHaveText("↑");
  await expect(page.locator('[data-sort-key="score"]').locator("..")).not.toHaveAttribute("aria-sort");
  await sort.press("Enter");
  await expect(symbols).toHaveText(["TECH", "NYSE", "NAS", "HIGH", "LOW"]);
  await expect(header).toHaveAttribute("aria-sort", "descending");
  await expect(sort.locator(".sort-indicator")).toHaveText("↓");
  await page.getByRole("button", { name: "Symbol", exact: true }).click();
  await expect(header).not.toHaveAttribute("aria-sort");
  await expect(sort.locator(".sort-indicator")).toHaveText("↕");
  await sort.click();
  await expect(symbols).toHaveText(["HIGH", "LOW", "NAS", "NYSE", "TECH"]);
});

for (const viewport of [{ width: 1440, height: 900 }, { width: 375, height: 900 }]) {
  test(`Discover and Overextended headers stay single-line at ${viewport.width}px`, async ({ page }) => {
    await page.route("**/auth/status", route => route.fulfill({ json: { authenticated: true } }));
    await page.route("**/health", route => route.fulfill({ json: { status: "ok", fmp_configured: false } }));
    await page.route("**/phase2/**", route => route.fulfill({ json: { results: [] } }));
    const results = [
      { symbol: "ZZZ", score: 95, last_close: 20, factors: { return_1m: 0.12, return_2w: 0.05, rsi_14: 61 } },
      { symbol: "AAA", score: 80, last_close: 10, factors: { return_1m: 0.08, return_2w: 0.04, rsi_14: 55 } },
      { symbol: "MMM", score: 70, last_close: 8, factors: { return_1m: 0.06 } },
    ];
    await page.route("**/phase2/candidates?*", route => route.fulfill({ json: { results, growth_min: 0, period_days: 30, limit: 200 } }));
    await page.setViewportSize(viewport);
    await page.goto("/");

    await expect(page.locator("#growthColumnLabel")).toHaveText("30D Growth");
    const assertHeaders = async selector => {
      const headers = page.locator(`${selector} thead th:not(.col-toggle-header)`);
      await expect(headers).not.toHaveCount(0);
      for (let i = 0; i < await headers.count(); i += 1) {
        const header = headers.nth(i);
        const metrics = await header.evaluate(th => {
          const walker = document.createTreeWalker(th, NodeFilter.SHOW_TEXT);
          const textNodes = [];
          while (walker.nextNode()) textNodes.push(walker.currentNode);
          const textRects = textNodes.flatMap(node => {
            const range = document.createRange();
            range.selectNodeContents(node);
            return [...range.getClientRects()];
          });
          const button = th.querySelector(".sort-button");
          const style = getComputedStyle(th);
          return {
            textTops: textRects.map(rect => rect.top),
            scrollWidth: th.scrollWidth,
            clientWidth: th.clientWidth,
            buttonScrollWidth: button?.scrollWidth ?? 0,
            contentWidth: th.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
            whiteSpace: style.whiteSpace,
          };
        });
        expect(metrics.whiteSpace).toBe("nowrap");
        expect(metrics.textTops.every(top => Math.abs(top - metrics.textTops[0]) <= 0.5)).toBe(true);
        expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
        expect(metrics.buttonScrollWidth).toBeLessThanOrEqual(metrics.contentWidth + 1);
      }
    };
    await assertHeaders(".discover-table");

    const symbolSort = page.getByRole("button", { name: "Symbol", exact: true });
    await symbolSort.click();
    await expect(page.locator("#discoverBody .symbol-link")).toHaveText(["AAA", "MMM", "ZZZ"]);
    const rankSort = page.getByRole("button", { name: "Rank", exact: true });
    await rankSort.click();
    await expect(page.locator("#discoverBody .symbol-link")).toHaveText(["ZZZ", "AAA", "MMM"]);
    await expect(rankSort.locator("..")).toHaveAttribute("aria-sort", "ascending");
    await expect(rankSort.locator(".sort-indicator")).toHaveText("↑");
    await rankSort.click();
    await expect(page.locator("#discoverBody .symbol-link")).toHaveText(["MMM", "AAA", "ZZZ"]);
    await expect(rankSort.locator("..")).toHaveAttribute("aria-sort", "descending");
    await expect(rankSort.locator(".sort-indicator")).toHaveText("↓");
    await page.getByRole("button", { name: "Score", exact: true }).click();
    await expect(rankSort.locator(".sort-indicator")).toHaveText("↕");
    await expect(rankSort.locator("..")).not.toHaveAttribute("aria-sort", /.+/);

    const rankHeader = page.locator(".discover-table thead th:nth-child(2)");
    const rankCell = page.locator("#discoverBody tr").first().locator("td").nth(1);
    const toggleHeader = page.locator(".discover-table thead th:nth-child(1)");
    const toggleCell = page.locator("#discoverBody tr").first().locator("td").first();
    for (const [header, cell] of [[rankHeader, rankCell], [toggleHeader, toggleCell]]) {
      const edges = await Promise.all([header, cell].map(locator => locator.boundingBox()));
      expect(Math.abs(edges[0].x - edges[1].x)).toBeLessThanOrEqual(1);
      expect(Math.abs(edges[0].width - edges[1].width)).toBeLessThanOrEqual(1);
    }

    await page.locator('button.tab-button[data-tab="overextendedPanel"]').click();
    await assertHeaders(".overextended-table");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
