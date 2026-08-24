module.exports = {
  testDir: ".",
  testMatch: "playwright-auth-smoke.spec.js",
  reporter: "line",
  use: {
    browserName: "chromium",
  },
};
