module.exports = {
  testDir: ".",
  testMatch: "playwright-landing.spec.js",
  reporter: "line",
  use: { browserName: "chromium", baseURL: process.env.SITE_URL || "http://127.0.0.1:8003" },
};
