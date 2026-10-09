const { defineConfig, devices } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "on-first-retry",
    screenshot: "only-on-failure"
  },
  webServer: {
    command: `${process.env.LINKDROP_PYTHON || ".venv/bin/python"} app.py`,
    url: "http://127.0.0.1:4173/api/health",
    env: { ...process.env, PORT: "4173" },
    reuseExistingServer: true,
    timeout: 30_000
  },
  projects: [
    {
      name: "desktop-chrome",
      use: { ...devices["Desktop Chrome"], channel: "chrome" }
    },
    {
      name: "mobile-chrome",
      use: { ...devices["Pixel 7"] }
    },
    {
      name: "mobile-safari",
      use: { ...devices["iPhone 13"] }
    }
  ]
});
