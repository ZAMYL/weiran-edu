import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { theme } from 'antd';

const origin = process.env.EDU_TEST_WEB ?? 'http://127.0.0.1:3489';
const browser = await chromium.launch({
  executablePath: process.env.EDU_TEST_BROWSER,
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(12_000);
const errors = [];
let uploadRequest;
page.on('pageerror', (error) => errors.push(error.message));

const assessment = {
  assessment_id: 'example',
  title: 'Python 基础测评',
  student_count: 0,
  problem_count: 0,
  review_state: 'ready',
};
const scope = {
  data_scope_id: 'scope_demo',
  label: '测试数据',
  can_import: true,
  can_view: true,
};
await page.route('**/api/edu/**', async (route) => {
  const url = new URL(route.request().url());
  const path = url.pathname.replace(/^\/api\/edu(?:\/v1\/edu)?\//, '');
  const offset = Number(url.searchParams.get('offset') ?? 0);
  const limit = Number(url.searchParams.get('limit') ?? 20);
  if (path === 'assessments' && route.request().method() === 'POST') {
    uploadRequest = route.request().postData();
    await route.fulfill({ json: { assessment_id: assessment.assessment_id, duplicate: false } });
    return;
  }
  const responses = {
    'test-session': { enabled: true },
    me: { teacher_id: 'teacher_navigation' },
    scopes: { items: [scope] },
    batches: { total_count: 0, items: [], page: { offset, limit, returned: 0, has_more: false } },
    assessments: { total: 1, items: [assessment] },
    'assessments/example': {
      ...assessment,
      source_snapshot_id: null,
      counts_provisional: false,
      assets: [{ asset_id: 'zip001', entry: '合成原始资料.zip', storage_state: 'unavailable' }],
      materials: Array.from({ length: 12 }, (_, index) => ({
        asset_id: 'zip001',
        entry: `合成答题材料-${index + 1}.xlsx`,
        needs_confirmation: index === 0,
      })),
      diagnostics: [
        { code: 'TEST_COLUMNS_INFERRED', entry: null, message: '内部列判断' },
        { code: 'OTHER', entry: null, message: '合成检查信息' },
      ],
    },
    'assessments/example/patterns': { assessment_id: 'example', items: [] },
    'assessments/example/students': {
      assessment_id: 'example',
      items: [
        {
          student_pk: 'synthetic-001',
          display_name: '合成学生 001',
          external_key: 'S001',
          started_at: null,
          finished_at: null,
        },
      ],
    },
  };
  if (Object.hasOwn(responses, path)) await route.fulfill({ json: responses[path] });
  else await route.fallback();
});

const menu = page.getByRole('menu', { name: '工作台导航' });
const topTabs = page.locator('.workspace-tabbar');
const tab = (name) => topTabs.getByRole('tab', { name, exact: true });
const uploadOption = (name, checked) =>
  page
    .locator('.upload-workflow > .ant-card-head')
    .getByRole('radio', { name, exact: true, checked });
const menuItem = (name) => menu.getByRole('menuitem', { name, exact: true });
const heading = (name) => page.getByRole('heading', { name, exact: true, level: 1 });
const selected = async (menuName, tabName) => {
  await tab(tabName).and(page.locator('[aria-selected="true"]')).waitFor();
  await menuItem(menuName).and(page.locator('.ant-menu-item-selected')).waitFor();
};
const close = (name) =>
  topTabs
    .locator('.ant-tabs-tab')
    .filter({ has: page.getByRole('tab', { name, exact: true }) })
    .getByRole('button');

try {
  await page.goto(origin);
  await page.getByRole('button', { name: '进入教师工作台', exact: true }).first().click();
  await page.waitForURL('**/dashboard');
  await heading('仪表盘').waitFor();
  if (process.env.EDU_TEST_SCREENSHOT)
    await page.screenshot({ path: process.env.EDU_TEST_SCREENSHOT });
  await selected('仪表盘', '仪表盘');
  assert.equal(await close('仪表盘').count(), 0);

  await menuItem('分析').click();
  await menuItem('学情分析').click();
  await heading('学情分析').waitFor();
  await selected('学情分析', '学情分析');
  await page.getByRole('link', { name: '历史测评', exact: true }).click();
  await selected('评分与评价', '历史测评');
  await page.getByText('已归档', { exact: true }).click();
  await page.getByRole('radio', { name: '已归档', checked: true }).waitFor({ state: 'attached' });

  await menuItem('上传').click();
  await page.waitForURL('**/upload');
  await selected('上传', '上传');
  assert.equal(await menuItem('上传').getAttribute('aria-expanded'), null);
  await uploadOption('评分与评价', true).waitFor({ state: 'attached' });
  await uploadOption('编程答题卡').locator('..').click();
  await page.waitForURL('**/upload/answer-sheets');
  await selected('上传', '上传');
  await uploadOption('编程答题卡', true).waitFor({ state: 'attached' });
  const tabStyle = await page
    .locator('.ant-tabs-tab')
    .first()
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        border: style.borderTopWidth,
        radius: style.borderTopLeftRadius,
        height: element.offsetHeight,
      };
    });
  assert.equal(tabStyle.border, '1px');
  assert.equal(tabStyle.radius, theme.getDesignToken().borderRadiusLG + 'px');
  assert.ok(tabStyle.height < 48);
  const [activeTabBackground, contentBackground] = await page.evaluate(() => {
    const active = document.querySelector('.workspace-tabbar .ant-tabs-tab-active');
    const content = document.querySelector('[role=tabpanel][aria-hidden=false] .workspace-scroll');
    return [getComputedStyle(active).backgroundColor, getComputedStyle(content).backgroundColor];
  });
  assert.equal(activeTabBackground, contentBackground);
  await page
    .getByRole('tabpanel', { name: '上传', exact: true })
    .locator('input[type="file"][accept=".zip"]')
    .setInputFiles({
      name: 'fixture.zip',
      mimeType: 'application/zip',
      buffer: Buffer.from('fixture'),
    });
  await page.getByText('fixture.zip', { exact: true }).waitFor();
  await uploadOption('评分与评价').locator('..').click();
  await page.waitForURL('**/upload/scores');
  assert.equal(await page.getByText('fixture.zip', { exact: true }).isVisible(), false);
  await page.goBack();
  await uploadOption('编程答题卡', true).waitFor({ state: 'attached' });
  await page.getByText('fixture.zip', { exact: true }).waitFor();

  await tab('仪表盘').click();
  await selected('仪表盘', '仪表盘');
  await page.setViewportSize({ width: 1440, height: 420 });
  const scroll = page.getByRole('tabpanel').locator('.workspace-scroll');
  await scroll.evaluate((element) => element.scrollTo(0, 140));
  await page.waitForFunction(
    () =>
      document.querySelector('[role=tabpanel][aria-hidden=false] .workspace-scroll')?.scrollTop > 0,
  );
  const dashboardOffset = await scroll.evaluate((element) => element.scrollTop);
  await page.getByRole('link', { name: '历史答题卡', exact: true }).click();
  await page.getByRole('link', { name: assessment.title, exact: true }).click();
  await page.waitForURL('**/data/answer-sheets/example');
  await selected('编程答题卡', assessment.title);
  await page
    .getByRole('row', { name: /合成学生 001/ })
    .getByRole('button', { name: '查看个人证据', exact: true })
    .waitFor();
  assert.equal(await topTabs.getByRole('tab').count(), 6);
  const back = page.getByRole('button', { name: '返回答题卡列表', exact: true });
  const [backBox, headingBox] = await Promise.all([
    back.boundingBox(),
    page.locator('.answer-sheet-detail-heading').boundingBox(),
  ]);
  assert.ok(
    backBox &&
      headingBox &&
      Math.abs(backBox.x + backBox.width - headingBox.x - headingBox.width) < 1,
  );
  const sources = page.getByRole('button', { name: '查看资料来源', exact: true });
  const patterns = page.getByText('群体错误统计', { exact: true });
  await sources.scrollIntoViewIfNeeded();
  const beforeSources = await patterns.boundingBox();
  await sources.click();
  const sourcesDrawer = page.getByRole('dialog', { name: '资料来源', exact: true });
  await sourcesDrawer.getByText('合成原始资料.zip（原件不可用）', { exact: true }).waitFor();
  await sourcesDrawer.getByText('合成答题材料-1.xlsx（待核查）', { exact: true }).waitFor();
  await sourcesDrawer.getByText('合成检查信息', { exact: true }).waitFor();
  assert.equal(await sourcesDrawer.getByText('内部列判断', { exact: true }).count(), 0);
  assert.equal((await patterns.boundingBox()).y, beforeSources.y);
  assert.ok(
    await sourcesDrawer
      .locator('.ant-drawer-body')
      .evaluate((el) => el.scrollHeight > el.clientHeight),
  );
  await sourcesDrawer.locator('.ant-pagination-next button').click();
  await sourcesDrawer.getByText('合成答题材料-12.xlsx', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await sourcesDrawer.waitFor({ state: 'hidden' });
  await sources.and(page.locator(':focus')).waitFor();
  await back.click();
  await page.waitForURL('**/data/answer-sheets');
  await selected('编程答题卡', '历史答题卡');
  await tab(assessment.title).click();
  await selected('编程答题卡', assessment.title);

  await tab('上传').click();
  await selected('上传', '上传');
  await uploadOption('编程答题卡', true).waitFor({ state: 'attached' });
  await page.getByText('fixture.zip', { exact: true }).waitFor();
  await page.goBack();
  await selected('编程答题卡', assessment.title);
  await page.goForward();
  await selected('上传', '上传');
  await page.getByRole('button', { name: '上传并查看结果' }).click();
  await selected('编程答题卡', assessment.title);
  assert.match(uploadRequest, /fixture\.zip/);
  await tab('仪表盘').click();
  await selected('仪表盘', '仪表盘');
  assert.equal(await scroll.evaluate((element) => element.scrollTop), dashboardOffset);
  await page.setViewportSize({ width: 1440, height: 900 });
  await tab('上传').click();
  await selected('上传', '上传');

  await close('学情分析').click();
  await selected('上传', '上传');
  await tab('学情分析').waitFor({ state: 'detached' });
  await close('上传').click();
  await selected('评分与评价', '历史测评');
  await page.getByRole('radio', { name: '已归档', checked: true }).waitFor({ state: 'attached' });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '展开侧栏', exact: true }).waitFor();
  await page.evaluate(() => {
    sessionStorage.setItem(
      'weiran.edu.workspace-tabs.v1',
      JSON.stringify({ paths: ['/dashboard', '/upload/scores', '/upload/answer-sheets'] }),
    );
  });
  await page.goto(`${origin}/dashboard`);
  await tab('上传').click();
  await page.waitForURL('**/upload/answer-sheets');
  assert.equal(await tab('上传').count(), 1);
  await uploadOption('编程答题卡', true).waitFor({ state: 'attached' });
  await page.reload();
  await uploadOption('编程答题卡', true).waitFor({ state: 'attached' });
  await uploadOption('评分与评价').locator('..').waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
  const themeButton = page.getByRole('button', { name: '暗黑主题' });
  const darkThemeButton = page.getByRole('button', { name: '暗黑主题', pressed: true });
  const card = page.locator('.upload-workflow:visible');
  const background = () => card.evaluate((element) => getComputedStyle(element).backgroundColor);
  const lightBackground = await background();
  await themeButton.press('Enter');
  await darkThemeButton.waitFor();
  await page.waitForFunction(
    () => getComputedStyle(document.documentElement).colorScheme === 'dark',
  );
  assert.notEqual(await background(), lightBackground);
  await page.reload();
  await darkThemeButton.waitFor();
  await uploadOption('编程答题卡', true).waitFor({ state: 'attached' });
  await themeButton.click();
  await page.waitForFunction(
    () => getComputedStyle(document.documentElement).colorScheme === 'light',
  );
  assert.equal(await background(), lightBackground);
  assert.deepEqual(errors, []);
  console.log(
    'PASS: sidebar, tabs, browser history, route details, preserved forms, responsive layout and dark theme.',
  );
} finally {
  await browser.close();
}
