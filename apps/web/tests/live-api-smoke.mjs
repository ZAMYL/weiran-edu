// Real FastAPI + SQLite + production Next.js + Chromium. No mocks on the happy path.
// Files/DB are synthetic and temporary; credentials exist only in process/browser memory.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from 'playwright';

const web = fileURLToPath(new URL('..', import.meta.url));
const repo = path.resolve(web, '../..'),
  apiRoot = path.join(repo, 'apps/api');
const python =
  process.env.EDU_TEST_PYTHON ||
  path.join(
    repo,
    '.local/api-venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );
assert.ok(existsSync(python), 'Prepare the locked API Python environment or set EDU_TEST_PYTHON.');
const temporary = await mkdtemp(path.join(tmpdir(), 'weiran-edu-browser-'));
const database = path.join(temporary, 'synthetic.sqlite3');
const artifacts = path.join(web, 'artifacts/live-api');
await mkdir(artifacts, { recursive: true });
const env = {
  ...process.env,
  PYTHONPATH: apiRoot,
  WEIRAN_EDU_ENV: 'test',
  WEIRAN_EDU_DATABASE: database,
  NEXT_TELEMETRY_DISABLED: '1',
};
function admin(...args) {
  return JSON.parse(
    execFileSync(python, ['-m', 'app.edu', '--database', database, ...args], {
      cwd: apiRoot,
      env,
      encoding: 'utf8',
      windowsHide: true,
    }),
  );
}
async function freePort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
const children = [];
function service(executable, args, cwd, environment) {
  const child = spawn(executable, args, {
    cwd,
    env: environment,
    stdio: ['ignore', 'ignore', 'ignore'],
    windowsHide: true,
  });
  children.push(child);
  return child;
}
async function ready(url, process) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    assert.equal(process.exitCode, null, 'Test service exited before becoming ready');
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(1500) })).ok) return;
    } catch {
      /* loopback readiness */
    }
    await sleep(250);
  }
  throw new Error('Test service startup timeout');
}
let browser, page;
const checks = [],
  pageErrors = [];
let checkpoint = 'setup';
try {
  admin('init');
  const teacher = admin('teacher-create', '--subject', 'synthetic-browser-teacher').teacher_id;
  const scope = admin('scope-create', '--label', '合成浏览器测试来源').data_scope_id;
  admin('member-set', '--teacher-id', teacher, '--scope-id', scope, '--can-import');
  let token = admin('token-issue', '--teacher-id', teacher, '--hours', '1').access_token;
  execFileSync(python, [path.join(web, 'tests/make-live-fixtures.py'), temporary], {
    cwd: apiRoot,
    env,
    windowsHide: true,
  });
  const apiPort = await freePort(),
    webPort = await freePort();
  const api = `http://127.0.0.1:${apiPort}`,
    origin = `http://127.0.0.1:${webPort}`;
  const backend = service(
    python,
    [
      '-m',
      'uvicorn',
      'app.main:create_app',
      '--factory',
      '--host',
      '127.0.0.1',
      '--port',
      String(apiPort),
      '--no-access-log',
    ],
    apiRoot,
    env,
  );
  await ready(`${api}/openapi.json`, backend);
  const openapi = await (await fetch(`${api}/openapi.json`)).json();
  assert.ok(openapi.paths['/api/v1/edu/batches/{batch_id}/insights/{candidate_id}/records/query']);
  assert.ok(openapi.components.schemas.Evidence.properties.comment_review_version);
  await writeFile(path.join(artifacts, 'openapi.runtime.json'), JSON.stringify(openapi, null, 2));
  const frontend = service(
    process.execPath,
    [
      'node_modules/next/dist/bin/next',
      'start',
      '--hostname',
      '127.0.0.1',
      '--port',
      String(webPort),
    ],
    web,
    { ...env, EDU_API_ORIGIN: api },
  );
  await ready(origin, frontend);
  const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  browser = await chromium.launch({
    headless: true,
    ...(existsSync(chrome) ? { executablePath: chrome } : {}),
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15_000);
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const shot = (name) =>
    page.screenshot({ path: path.join(artifacts, `${name}.png`), fullPage: true });
  const noOverflow = async () =>
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
      'No document overflow',
    );
  const navigation = (name) => page.getByRole('link', { name, exact: true }).click();
  async function login() {
    await page.getByRole('button', { name: '连接教师会话', exact: true }).click();
    await page.getByLabel('教师临时凭证', { exact: true }).fill(token);
    await page.getByRole('dialog').getByRole('button', { name: '连接', exact: true }).click();
    await page.getByRole('button', { name: '结束会话', exact: true }).waitFor();
    await page.locator('#teacher-access-token').waitFor({ state: 'detached' });
  }
  async function upload(kind, title) {
    await page.getByLabel('测评名称', { exact: true }).fill(title);
    await page.locator('input[type=file]').setInputFiles(path.join(temporary, `${kind}.xlsx`));
    await page.getByRole('heading', { name: '正式导入预览' }).waitFor();
    await page.locator('.source-details').filter({ hasText: '预览版本' }).waitFor();
  }
  async function commit() {
    const pending = page.waitForResponse(
      (r) => r.url().endsWith('/commit') && [200, 201].includes(r.status()),
    );
    await page.getByRole('button', { name: '确认入库', exact: true }).click();
    const data = await (await pending).json();
    await page.getByRole('heading', { name: '上传正式测评' }).waitFor();
    return data;
  }
  async function request(route, init = {}) {
    const response = await fetch(`${api}/api/v1/edu${route}`, {
      ...init,
      headers: {
        authorization: `Bearer ${token}`,
        ...(typeof init.body === 'string' ? { 'content-type': 'application/json' } : {}),
        ...init.headers,
      },
    });
    const data = await response.json();
    assert.ok(response.ok, `Synthetic backend request failed with ${response.status}`);
    return data;
  }
  async function selectBatch(title) {
    await page.getByRole('combobox', { name: '当前正式测评批次' }).click();
    await page.locator('.ant-select-dropdown').getByText(title, { exact: true }).click();
  }
  checkpoint = 'login';
  await page.goto(origin);
  await login();
  await navigation('测评数据');
  checkpoint = 'blocked preview';
  await upload('blocked', '合成阻断测评');
  await page.getByText('存在阻断错误，无法确认入库', { exact: true }).waitFor();
  assert.equal(
    await page.getByRole('button', { name: '确认入库', exact: true }).isDisabled(),
    true,
  );
  await page.getByRole('button', { name: '定位记录', exact: true }).first().click();
  await page.locator('.located-row').waitFor();
  // Independent issue and row pages both retain full-file counts.
  await page.locator('.issue-list').locator('..').getByTitle('2', { exact: true }).click();
  await page.locator('.issue-list').getByRole('heading').first().waitFor();
  await shot('blocked-preview');
  await page.getByRole('button', { name: '返回上传', exact: true }).click();
  checks.push('blocked dual-Sheet upload, full quality report, issue pagination and row location');
  checkpoint = 'warning version';
  await upload('warning', '合成警告测评');
  const confirm = page.getByRole('button', { name: '确认入库', exact: true });
  assert.equal(await confirm.isDisabled(), true);
  await page.getByRole('checkbox', { name: /我已查看本预览版本/ }).check();
  await page.getByLabel('正式测评名称', { exact: true }).fill('合成警告测评已确认');
  // Hold the real PATCH so the loading icon is present while checking the action name.
  let releasePatch;
  const patchGate = new Promise((resolve) => (releasePatch = resolve));
  const patchRoute = async (route) => {
    if (route.request().method() === 'PATCH') await patchGate;
    await route.continue();
  };
  await page.route('**/api/edu/imports/*', patchRoute);
  const patchStarted = page.waitForRequest((r) => r.method() === 'PATCH');
  const patched = page.waitForResponse((r) => r.request().method() === 'PATCH');
  await page.getByRole('button', { name: '保存名称并更新预览版本' }).click();
  await patchStarted;
  try {
    assert.equal(await confirm.isDisabled(), true);
  } finally {
    releasePatch();
  }
  assert.equal((await patched).status(), 200);
  await page.unroute('**/api/edu/imports/*', patchRoute);
  await page.getByRole('button', { name: '保存名称并更新预览版本' }).waitFor({ state: 'detached' });
  assert.equal(await page.getByRole('checkbox', { name: /我已查看本预览版本/ }).isChecked(), false);
  assert.equal(await confirm.isDisabled(), true);
  await page.getByRole('checkbox', { name: /我已查看本预览版本/ }).check();
  const warningCommit = await commit();
  assert.equal(warningCommit.result, 'created');
  const replay = await request(`/imports/${warningCommit.import_id}/commit`, {
    method: 'POST',
    body: JSON.stringify({
      preview_version: (await request(`/imports/${warningCommit.import_id}`)).import
        .preview_version,
      acknowledge_warnings: true,
    }),
  });
  assert.equal(replay.result, 'replayed');
  checkpoint = 'normal and duplicate';
  await upload('normal', '合成正常测评');
  const normalCommit = await commit();
  assert.equal(normalCommit.result, 'created');
  await upload('normal', '合成重复上传');
  assert.equal((await commit()).result, 'duplicate');
  assert.equal((await request('/batches')).total_count, 2);
  checks.push(
    'warning acknowledgement tied to updated preview version; created/duplicate/replayed results',
  );
  checkpoint = 'statistics';
  await navigation('学情洞察');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  assert.match(await page.locator('.stat-grid').innerText(), /23/);
  await shot('insights-desktop');
  await selectBatch('合成警告测评已确认');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  await selectBatch('合成正常测评');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  checkpoint = 'group pagination';
  await navigation('画像探索');
  await page.locator('.match-summary').filter({ hasText: '23 / 23' }).waitFor();
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await page.getByRole('combobox', { name: '正式条件1比较' }).click();
  await page.locator('.ant-select-dropdown').getByText('=', { exact: true }).click();
  await page.getByRole('textbox', { name: '正式条件1评分' }).fill('2.751');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.getByText('评分阈值须为 1–5，最多两位有效小数；原输入不会自动舍入。').waitFor();
  assert.equal(await page.getByRole('textbox', { name: '正式条件1评分' }).inputValue(), '2.751');
  await page.getByRole('textbox', { name: '正式条件1评分' }).fill('2.75');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '12 / 23' }).waitFor();
  await page.locator('.ant-table-wrapper').getByTitle('2', { exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll('.ant-table-tbody tr[data-row-key]').length === 2,
  );
  assert.match(await page.locator('.match-summary').innerText(), /12 \/ 23/);
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await page.getByRole('textbox', { name: '正式条件2评分' }).fill('3.25');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '12 / 23' }).waitFor();
  checkpoint = 'student source';
  const sourceResponse = page.waitForResponse((r) => /\/evidence\//.test(r.url()));
  await page
    .getByRole('button', { name: /查看 .* 正式详情/ })
    .first()
    .click();
  await page.getByText('原始评语暂未开放（非空，待审核）', { exact: true }).first().waitFor();
  assert.equal(await page.locator('.ant-drawer-open .source-details').count(), 5);
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  await page
    .getByRole('button', { name: /算法与数据结构 2.75 分证据/ })
    .first()
    .click();
  await page.getByText('Sheet1 · L3:P6', { exact: true }).waitFor();
  const source = await (await sourceResponse).json();
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  checkpoint = 'published display compatibility';
  const reviewBase = `/batches/${normalCommit.batch_id}/text-reviews`;
  const writeReview = (kind, target, expected, status, displayText) =>
    request(`${reviewBase}/${kind}/${target}`, {
      method: 'PUT',
      body: JSON.stringify({
        snapshot_id: source.snapshot_id,
        expected_version: expected,
        status,
        ...(displayText ? { display_text: displayText } : {}),
      }),
    });
  const publishedComment = await writeReview(
    'comment',
    source.evidence_id,
    0,
    'published',
    '合成已审核评语：结构清晰。',
  );
  const publishedRubric = await writeReview(
    'rubric',
    source.rubric_criterion_id,
    0,
    'published',
    '合成已审核依据：仅用于本批评分解释。',
  );
  await page
    .getByRole('button', { name: /算法与数据结构 2.75 分证据/ })
    .first()
    .click();
  await page.getByText(publishedComment.display_text, { exact: true }).waitFor();
  await page.getByText(publishedRubric.display_text, { exact: true }).waitFor();
  await shot('published-evidence-desktop');
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  await writeReview('comment', source.evidence_id, 1, 'withheld');
  await writeReview('rubric', source.rubric_criterion_id, 1, 'withheld');
  await page
    .getByRole('button', { name: /算法与数据结构 2.75 分证据/ })
    .first()
    .click();
  await page.getByText('原始评语暂未开放（非空，待审核）', { exact: true }).waitFor();
  assert.equal(await page.getByText(publishedComment.display_text, { exact: true }).count(), 0);
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  checks.push(
    'latest runtime published text/rubric versions render; reopening after withdrawal refetches and hides text',
  );
  await shot('explorer-desktop');
  checks.push(
    'server full-batch mean 3.11; exact 2.75 group 12/23, second page 2 items; withheld and authorized source/rubric',
  );
  checkpoint = 'metric query replay';
  await page.getByRole('button', { name: '查看这组评分洞察' }).click();
  await page.locator('.live-candidate').first().waitFor();
  const batch = await request(`/batches/${normalCommit.batch_id}`);
  const spec = {
    snapshot_id: batch.snapshot_id,
    conditions: [
      { dimension_code: 'algorithm_data_structure', operator: 'eq', value: 2.75 },
      { dimension_code: 'code_implementation', operator: 'gte', value: 3.25 },
    ],
    include_dimensions: true,
  };
  const insight = await request(`/batches/${batch.batch_id}/insights/query`, {
    method: 'POST',
    body: JSON.stringify(spec),
  });
  const card = insight.items.find((c) => c.dimension_code === 'algorithm_data_structure');
  assert.deepEqual(card.conditions, []); // This must not replace the original query conditions.
  const metric = card.metrics.find((m) => m.key === 'mean');
  assert.ok(metric);
  const recordsResponse = page.waitForResponse((r) => r.url().includes('/records/query'));
  await page.getByRole('button', { name: `${card.title} mean 查看评分证据`, exact: true }).click();
  const recordsHTTP = await recordsResponse,
    posted = recordsHTTP.request().postDataJSON();
  assert.deepEqual(posted.conditions, spec.conditions);
  assert.equal(posted.snapshot_id, spec.snapshot_id);
  assert.equal(posted.include_dimensions, true);
  const records = await recordsHTTP.json();
  await page
    .getByText(
      `指标来源记录数：${records.metric.source_record_count} · 参与记录总数：${records.total_count}`,
      { exact: true },
    )
    .waitFor();
  assert.equal(records.metric.value.exact.denominator, 46);
  assert.equal(records.metric.denominator, 23);
  await page.getByText('未作为本指标证据', { exact: true }).first().waitFor();
  await shot('metric-evidence-desktop');
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  checks.push(
    'insight metric drilldown replays original conditions/snapshot/include_dimensions; rational denominator is not headcount',
  );
  checkpoint = 'empty group and insight';
  await navigation('画像探索');
  await page.getByRole('textbox', { name: '正式条件1评分' }).fill('5');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '0 / 23' }).waitFor();
  await page.getByText('没有匹配记录，请调整筛选条件。', { exact: true }).waitFor();
  await navigation('学情洞察');
  const include = page.getByRole('checkbox', { name: '包含各维度洞察' });
  await include.uncheck();
  // Empty group candidates may be insufficient-data cards; no-candidate empty is tested with zero conditions + dimensions disabled.
  await navigation('画像探索');
  await page.getByRole('button', { name: '重置', exact: true }).click();
  await navigation('学情洞察');
  await page.getByRole('checkbox', { name: '包含各维度洞察' }).uncheck();
  await page.getByText('当前查询没有可展示的评分洞察候选。', { exact: true }).waitFor();
  checks.push('zero-match group and genuine no-candidate insight states');
  checkpoint = 'error states';
  await navigation('画像探索');
  for (const [status, code] of [
    [403, 'FORBIDDEN'],
    [404, 'NOT_FOUND'],
    [409, 'SNAPSHOT_MISMATCH'],
    [410, 'IMPORT_EXPIRED'],
    [413, 'REQUEST_TOO_LARGE'],
    [415, 'UNSUPPORTED_MEDIA_TYPE'],
    [422, 'VALIDATION_ERROR'],
    [503, 'SYNTHETIC_BUSY'],
    [504, 'REQUEST_TIMEOUT'],
  ]) {
    const pattern = '**/api/edu/batches/*/statistics?*';
    await page.route(pattern, (route) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: { 'x-request-id': 'req_synthetic' },
        body: JSON.stringify({ error: { code, message: '合成私密输入不得回显' } }),
      }),
    );
    await navigation('学情洞察');
    await page.locator('.live-error').filter({ hasText: code }).waitFor();
    assert.equal(await page.getByText('合成私密输入不得回显', { exact: true }).count(), 0);
    await page.unroute(pattern);
    await navigation('画像探索');
  }
  const statsPattern = '**/api/edu/batches/*/statistics?*';
  await page.route(statsPattern, (route) => route.abort('failed'));
  await navigation('学情洞察');
  await page.locator('.live-error').filter({ hasText: 'NETWORK_ERROR' }).waitFor();
  await page.unroute(statsPattern);
  checks.push('safe 403/404/409/410/413/415/422/503 and network failure UI, with no Demo fallback');
  checkpoint = 'large sparse batch';
  const form = new FormData();
  form.append('data_scope_id', scope);
  form.append('title', '合成401评分测评');
  form.append('file', new Blob([await readFile(path.join(temporary, 'large.xlsx'))]), 'large.xlsx');
  const largePreview = await request('/imports/preview', { method: 'POST', body: form });
  const largeCommit = await request(`/imports/${largePreview.import.import_id}/commit`, {
    method: 'POST',
    body: JSON.stringify({
      preview_version: largePreview.import.preview_version,
      acknowledge_warnings: false,
    }),
  });
  const largeBatch = await request(`/batches/${largeCommit.batch_id}`);
  const largeStats = await request(
    `/batches/${largeBatch.batch_id}/statistics?snapshot_id=${largeBatch.snapshot_id}&x_dimension=algorithm_data_structure&y_dimension=solution_strategy`,
  );
  assert.equal(largeStats.matrix.cells.length, 401);
  await navigation('测评数据');
  await page.getByRole('button', { name: /刷新批次/ }).click();
  const largeRow = page.getByRole('row').filter({ hasText: '合成401评分测评' });
  await largeRow.getByRole('button', { name: '查看学情' }).click();
  await page.getByRole('img', { name: /算法与数据结构均值3.00/ }).waitFor();
  assert.ok(
    (await page.locator('.live-metrics button').count()) < 80,
    'Progressively disclose 401 score buckets',
  );
  await navigation('画像探索');
  await page
    .getByRole('img', { name: '服务端稀疏矩阵，401 个实际评分组合，配对人数 401' })
    .waitFor();
  assert.ok(
    (await page.locator('.sparse-matrix svg path').count()) < 1000,
    'No Cartesian expansion',
  );
  const axisLabels = await page.locator('.sparse-matrix svg text').allTextContents();
  assert.ok(axisLabels.includes('1') && axisLabels.includes('5'), 'Readable 1–5 score axes');
  await shot('401-sparse-scores');
  checks.push('401 distinct legal scores rendered as 401 sparse cells without full-grid expansion');
  checkpoint = 'batch request race';
  let release;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  const oldPattern = `**/api/edu/batches/${largeBatch.batch_id}/statistics?*`;
  let oldRequested;
  const requested = new Promise((resolve) => {
    oldRequested = resolve;
  });
  await page.route(oldPattern, async (route) => {
    const response = await route.fetch();
    oldRequested();
    await held;
    await route.fulfill({ response }).catch(() => {});
  });
  await navigation('学情洞察');
  await requested;
  await selectBatch('合成正常测评');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  release();
  await page.unroute(oldPattern);
  await sleep(400);
  assert.match(await page.locator('.stat-grid').innerText(), /23/);
  assert.doesNotMatch(await page.locator('.stat-grid').innerText(), /401/);
  checks.push('late old-batch response cannot overwrite selected batch');
  checkpoint = 'mobile';
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, artifact] of [
    ['学情洞察', 'insights-mobile'],
    ['画像探索', 'explorer-mobile'],
    ['测评数据', 'data-mobile'],
  ]) {
    await navigation(name);
    await page
      .locator('.live-loading')
      .waitFor({ state: 'detached' })
      .catch(() => {});
    await noOverflow();
    await shot(artifact);
  }
  checks.push('390px three-page layout without document overflow');
  checkpoint = 'real permission revocation';
  admin('member-set', '--teacher-id', teacher, '--scope-id', scope); // Cached UI cannot override server-side import revocation.
  await page.getByLabel('测评名称', { exact: true }).fill('合成权限测试');
  await page.locator('input[type=file]').setInputFiles(path.join(temporary, 'normal.xlsx'));
  await page.getByText('当前教师没有执行此操作的权限。', { exact: true }).waitFor();
  admin('token-revoke', '--teacher-id', teacher);
  await page.getByRole('button', { name: /刷新批次/ }).click();
  await page.getByRole('heading', { name: '连接教师会话，查看正式测评数据' }).waitFor();
  assert.equal(await page.locator('.live-workspace').count(), 0);
  checks.push(
    'actual server 403 after import-permission revocation; actual 401 clears teacher access and all formal data',
  );
  checkpoint = 'refresh and persistence';
  token = admin('token-issue', '--teacher-id', teacher, '--hours', '1').access_token;
  await login();
  await page.getByRole('row').filter({ hasText: '合成正常测评' }).waitFor();
  const storage = await page.evaluate(() => ({
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
    cookies: document.cookie,
    url: location.href,
    html: document.documentElement.outerHTML,
  }));
  assert.ok(Object.values(storage).every((value) => !value.includes(token)));
  await page.reload();
  await page.getByRole('heading', { name: '连接教师会话，查看正式测评数据' }).waitFor();
  await login();
  await page.getByRole('row').filter({ hasText: '合成正常测评' }).waitFor();
  await page.getByText('Demo', { exact: true }).click();
  await page.getByRole('heading', { name: '上传新测评' }).waitFor();
  await page.getByText('正式数据', { exact: true }).click();
  await page.getByRole('heading', { name: '连接教师会话，查看正式测评数据' }).waitFor();
  checks.push(
    'DB batches survive refresh; token absent from storage/URL/DOM; Demo transition clears formal session',
  );
  assert.deepEqual(pageErrors, []);
  await writeFile(
    path.join(artifacts, 'result.json'),
    JSON.stringify({ checks, pageErrors }, null, 2),
  );
  console.log(JSON.stringify({ checks, pageErrors }, null, 2));
} catch (error) {
  if (page)
    await page
      .screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true })
      .catch(() => {});
  // Do not dump requests, headers, process environments, or credentials on failure.
  console.error(`Real API smoke failed at: ${checkpoint}`);
  console.error(error.message);
  process.exitCode = 1;
} finally {
  await browser?.close();
  for (const child of children.reverse()) {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
    }
  }
  await rm(temporary, { recursive: true, force: true });
}
