import assert from 'node:assert/strict';

// Run in student view with an authorized, synthetic assessment of more than 20 students.
// Delay the real response without replacing its payload, and measure during loading as well.
export async function portraitPaginationFlow(page) {
  const workspace = page.locator('.student-portrait-workspace');
  const pager = page.locator('.portrait-member-panel .ant-pagination');
  const input = pager.locator('.ant-pagination-simple-pager input');
  const pageCount = Number((await pager.innerText()).match(/\/\s*(\d+)/)?.[1]);
  assert.ok(pageCount > 2, 'assessment needs multiple pages and a final page');
  const measurements = [];
  const ready = () =>
    page.waitForFunction(
      () =>
        document.querySelector('.student-portrait-workspace')?.getAttribute('aria-busy') ===
        'false',
    );
  async function jump(target) {
    if (Number(await input.inputValue()) === target) return;
    await input.fill(String(target));
    await input.press('Enter');
    await ready();
  }
  for (const [width, height] of [
    [1440, 900],
    [1920, 921],
    [1920, 1080],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    await jump(1);
    for (const target of [2, pageCount, pageCount - 1]) {
      if (width > 760) {
        await page.getByRole('list', { name: '选择学生画像' }).getByRole('button').first().click();
      } else {
        await page.getByRole('combobox', { name: '选择当前页学生画像', exact: true }).click();
        await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
        await page.locator('.ant-select-dropdown:visible').waitFor({ state: 'hidden' });
      }
      await page.getByTestId('student-portrait-content').waitFor();
      const portrait = page.getByTestId('student-portrait-content');
      const selectedId = await portrait.getAttribute('data-student-id');
      const scores = await portrait.locator('.student-portrait-scores').innerText();
      await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
      await pager.scrollIntoViewIfNeeded();
      const current = Number(await input.inputValue());
      const direction = target === current + 1 ? 'next' : target === current - 1 ? 'prev' : null;
      if (!direction) await input.fill(String(target));
      const before = await page.evaluate(() => scrollY);
      let release;
      const gate = new Promise((resolve) => {
        release = resolve;
      });
      let intercepted;
      const requestStarted = new Promise((resolve) => {
        intercepted = resolve;
      });
      const pattern = '**/api/edu/**';
      const pendingRoutes = [];
      let portraitRequests = 0;
      const handler = async (route) => {
        const work = (async () => {
          if (/\/students\/[^/?]+\/evidence\?/.test(route.request().url())) portraitRequests++;
          if (/\/students\?|\/records\/query/.test(route.request().url())) {
            intercepted();
            await gate;
          }
          await route.continue();
        })();
        pendingRoutes.push(work);
        await work;
      };
      await page.route(pattern, handler);
      try {
        if (direction) await pager.locator(`.ant-pagination-${direction}`).click();
        else await input.press('Enter');
        await requestStarted;
        await page.waitForTimeout(150);
        const loading = await page.evaluate(() => scrollY);
        assert.ok(
          Math.abs(loading - before) <= 1,
          `loading changed scroll: ${before} → ${loading}`,
        );
        assert.equal(await workspace.getAttribute('aria-busy'), 'true');
        assert.equal(await portrait.getAttribute('data-student-id'), selectedId);
        assert.equal(await portrait.locator('.student-portrait-scores').innerText(), scores);
        assert.equal(portraitRequests, 0, 'pagination does not reload the retained portrait');
        assert.equal(await input.isDisabled(), true, 'pagination stays mounted during loading');
        // A slow response must not undo a teacher's own scrolling either.
        await page.evaluate(() => scrollBy(0, -32));
        const expected = await page.evaluate(() => scrollY);
        release();
        await ready();
        await page.waitForTimeout(200);
        const after = await page.evaluate(() => scrollY);
        assert.ok(
          Math.abs(after - expected) <= 1,
          `response changed scroll: ${expected} → ${after}`,
        );
        assert.equal(await portrait.getAttribute('data-student-id'), selectedId);
        assert.equal(await portrait.locator('.student-portrait-scores').innerText(), scores);
        assert.equal(
          await page.locator('.portrait-member-list button[aria-pressed="true"]').count(),
          0,
          'a retained portrait is not highlighted as a student on another page',
        );
        assert.equal(
          await page.locator('.portrait-member-select .ant-select-selection-item').count(),
          0,
          'the mobile picker does not display an out-of-page student ID',
        );
        assert.equal(await input.inputValue(), String(target));
        assert.equal(
          await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
          false,
        );
        measurements.push({ width, target, before, loading, expected, after });
      } finally {
        release();
        await Promise.all(pendingRoutes);
        await page.unroute(pattern, handler);
      }
    }
  }
  return measurements;
}
