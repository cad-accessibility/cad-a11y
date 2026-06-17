const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/a11y',
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: process.env.AXE_TARGET_URL || 'http://127.0.0.1:5050',
    headless: true,
  },
});
