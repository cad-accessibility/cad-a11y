const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

test('homepage has no serious or critical axe violations', async ({ page, baseURL }) => {
  await page.goto(baseURL || '/');

  const results = await new AxeBuilder({ page }).analyze();
  const blocking = results.violations.filter((violation) =>
    ['serious', 'critical'].includes(violation.impact)
  );

  if (blocking.length > 0) {
    for (const violation of blocking) {
      console.error(`- ${violation.id} (${violation.impact}): ${violation.help}`);
      for (const node of violation.nodes) {
        console.error(`  target: ${node.target.join(', ')}`);
      }
    }
  }

  expect(blocking, 'serious/critical axe violations').toEqual([]);
});
