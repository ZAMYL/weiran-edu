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
import { teacherAnalysisFlow, saveEmptyAnalysis } from './teacher-analysis-flow.mjs';
import { renameImport } from './upload-workflow-utils.mjs';

const web = fileURLToPath(new URL('..', import.meta.url));
const repo = path.resolve(web, '../..'),
  apiRoot = process.env.EDU_TEST_API_ROOT || path.join(repo, 'apps/api');
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
const testMode = process.env.EDU_BROWSER_TEST_SESSION === '1';
const testSessionFile = path.join(temporary, 'test-session.private.json');
const artifacts = path.join(web, testMode ? 'artifacts/test-session' : 'artifacts/live-api');
await mkdir(artifacts, { recursive: true });
const env = {
  ...process.env,
  PYTHONPATH: apiRoot,
  WEIRAN_EDU_ENV: 'test',
  WEIRAN_EDU_DATABASE: database,
  NEXT_TELEMETRY_DISABLED: '1',
  EDU_ENABLE_DEMO: '1',
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
  if (testMode)
    await writeFile(testSessionFile, JSON.stringify({ synthetic_only: true, access_token: token }));
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
    {
      ...env,
      EDU_API_ORIGIN: api,
      EDU_TEST_MODE: testMode ? '1' : '0',
      EDU_TEST_SESSION_FILE: testSessionFile,
    },
  );
  await ready(origin, frontend);
  const chrome = [
    process.env.EDU_TEST_BROWSER,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    process.env.LOCALAPPDATA &&
      path.join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe'),
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  ].find((value) => value && existsSync(value));
  browser = await chromium.launch({
    headless: true,
    ...(chrome ? { executablePath: chrome } : {}),
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15_000);
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const browserAuthorization = [];
  page.on('request', (request) => {
    if (testMode && request.url().includes('/api/edu/') && request.headers().authorization)
      browserAuthorization.push(true);
  });
  const shot = async (name) =>
    page.screenshot({
      path: path.join(artifacts, `${name}.png`),
      fullPage: (await page.locator('.ant-drawer-open').count()) === 0,
    });
  const noOverflow = async () =>
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
      'No document overflow',
    );
  const navigation = async (name) => {
    if (name === '测评数据') {
      if (await page.locator('.upload-workspace-heading').count()) {
        const reselect = page.getByRole('button', { name: '更换文件', exact: true });
        if (await reselect.count()) await reselect.click();
      } else await page.getByRole('button', { name: '上传测评资料', exact: true }).click();
    } else {
      await page.getByRole('link', { name, exact: true }).click();
      if (name === '学情诊断')
        await page.getByRole('radio', { name: '评分统计', exact: true }).locator('..').click();
    }
  };
  async function login(retryTestSession = false) {
    if (testMode) {
      const retry = page.getByRole('button', { name: '重新连接', exact: true });
      if (retryTestSession) await retry.click();
      await page.locator('.live-workspace').waitFor();
      assert.equal(await page.locator('#teacher-access-token').count(), 0);
      assert.equal(await page.getByRole('button', { name: '连接教师', exact: true }).count(), 0);
      return;
    }
    await page.locator('.session-gate').getByRole('button', { name: '登录', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '教师登录', exact: true });
    await dialog.getByRole('button', { name: '使用访问凭据', exact: true }).click();
    await dialog.getByLabel('访问凭据', { exact: true }).fill(token);
    await dialog.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('button', { name: '结束会话', exact: true }).waitFor();
    await page.locator('#teacher-access-token').waitFor({ state: 'detached' });
  }
  async function upload(kind, title, filename = title) {
    await navigation('测评数据');
    assert.equal(
      await page.getByRole('button', { name: '选择文件', exact: true }).isEnabled(),
      true,
    );
    const previewHTTP = page.waitForResponse((r) => r.url().endsWith('/imports/preview'));
    await page.locator('input[type=file]').setInputFiles({
      name: `${filename}.xlsx`,
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: await readFile(path.join(temporary, `${kind}.xlsx`)),
    });
    const uploaded = await previewHTTP;
    assert.equal((await uploaded.json()).import.title, filename);
    await page.getByRole('heading', { name: '文件检查结果', exact: true }).waitFor();
    assert.equal(await page.getByLabel('测评名称', { exact: true }).inputValue(), filename);
    await renameImport(page, title);
    await page
      .locator('.source-details')
      .filter({ hasText: '预览版本' })
      .waitFor({ state: 'attached' });
    assert.equal(await page.getByLabel('测评名称', { exact: true }).inputValue(), title);
  }
  async function commit() {
    const next = page.getByRole('button', { name: '下一步：确认导入', exact: true });
    if (await next.count()) await next.click();
    const pending = page.waitForResponse(
      (r) => r.url().endsWith('/commit') && [200, 201].includes(r.status()),
    );
    await page.getByRole('button', { name: '确认导入', exact: true }).click();
    const data = await (await pending).json();
    await page.locator('.discoveries-panel').waitFor();
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
    await page.getByRole('combobox', { name: '当前测评批次' }).click();
    await page.locator('.ant-select-dropdown').getByText(title, { exact: true }).click();
  }
  async function selectAspect(label) {
    await page.getByRole('combobox', { name: '查看洞察方面', exact: true }).click();
    await page.locator('.ant-select-dropdown:visible').getByText(label, { exact: true }).click();
  }
  checkpoint = 'login';
  await page.goto(origin);
  await login();
  await navigation('测评数据');
  assert.equal(await page.getByLabel('测评名称', { exact: true }).count(), 0);
  checkpoint = 'automatic file check and rename';
  const selectedBytes = await readFile(path.join(temporary, 'normal.xlsx'));
  let autoChecks = 0;
  let earlyCommits = 0;
  const trackChecks = (request) => {
    if (request.url().endsWith('/imports/preview')) autoChecks++;
    if (request.url().endsWith('/commit')) earlyCommits++;
  };
  page.on('request', trackChecks);
  for (const filename of ['合成首次选择.xlsx', '合成再次选择.XLSX']) {
    const checked = page.waitForResponse((r) => r.url().endsWith('/imports/preview'));
    await page.locator('input[type=file]').setInputFiles({
      name: filename,
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: selectedBytes,
    });
    assert.equal((await checked).status(), 201);
    await page.getByLabel('测评名称', { exact: true }).waitFor();
    assert.equal(
      await page.getByLabel('测评名称', { exact: true }).inputValue(),
      filename.replace(/\.xlsx$/i, ''),
    );
    await page.getByLabel('测评名称', { exact: true }).fill('');
    await page.getByLabel('测评名称', { exact: true }).press('Tab');
    assert.equal(
      await page.getByLabel('测评名称', { exact: true }).inputValue(),
      filename.replace(/\.xlsx$/i, ''),
    );
    await renameImport(page, '合成手动改名');
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow();
    await shot('upload-name-mobile');
    await page.getByRole('button', { name: '更换文件', exact: true }).click();
    assert.equal(await page.getByLabel('测评名称', { exact: true }).count(), 0);
  }
  assert.equal(autoChecks, 2, 'each selected file starts one automatic check');
  assert.equal(earlyCommits, 0, 'checking and renaming never import a file');
  page.off('request', trackChecks);
  await page.setViewportSize({ width: 1440, height: 1000 });
  checks.push(
    'selecting a file checks once automatically; name defaults to filename, edits save on blur; changing files never commits',
  );
  checkpoint = 'blocked preview';
  await upload('blocked', '合成阻断测评');
  const blockedFrame = await page.locator('.upload-workflow').boundingBox();
  await page.getByText('存在阻断错误，无法确认入库', { exact: true }).waitFor();
  assert.equal(
    await page.getByRole('button', { name: '下一步：确认导入', exact: true }).isDisabled(),
    true,
  );
  await page.locator('.issue-group details > summary').first().click();
  await page
    .locator('.issue-group')
    .getByRole('button', { name: '定位记录', exact: true })
    .first()
    .click();
  await page.locator('.located-row').waitFor();
  // Independent issue and row pages both retain full-file counts.
  await page.locator('.all-issues > summary').click();
  await page.locator('.all-issues').getByTitle('2', { exact: true }).click();
  await page.locator('.all-issues .ant-table-row').first().waitFor();
  const expandedFrame = await page.locator('.upload-workflow').boundingBox();
  assert.ok(
    Math.abs(blockedFrame.height - expandedFrame.height) <= 1,
    'expanded issues do not resize the upload frame',
  );
  assert.ok(
    await page
      .locator('.upload-workflow-body')
      .evaluate((element) => element.scrollHeight > element.clientHeight),
    'details scroll inside the single content region',
  );
  await shot('blocked-preview');
  await page.getByRole('button', { name: '更换文件', exact: true }).click();
  checks.push('blocked dual-Sheet upload, full quality report, issue pagination and row location');
  checkpoint = 'warning version';
  await upload('warning', '合成警告测评', '合成警告原文件名');
  const confirm = page.getByRole('button', { name: '下一步：确认导入', exact: true });
  assert.equal(await confirm.isDisabled(), true);
  await page.getByRole('checkbox', { name: /我已查看以上提醒/ }).check();
  await page.getByLabel('测评名称', { exact: true }).fill('合成警告测评已确认');
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
  await page.getByLabel('测评名称', { exact: true }).press('Tab');
  await patchStarted;
  try {
    assert.equal(await confirm.isDisabled(), true);
  } finally {
    releasePatch();
  }
  assert.equal((await patched).status(), 200);
  await page.unroute('**/api/edu/imports/*', patchRoute);
  await page.waitForFunction(
    () => document.querySelector('.upload-workflow')?.getAttribute('aria-busy') === 'false',
  );
  assert.equal(await page.getByRole('checkbox', { name: /我已查看以上提醒/ }).isChecked(), false);
  assert.equal(await confirm.isDisabled(), true);
  await page.getByRole('checkbox', { name: /我已查看以上提醒/ }).check();
  const warningCommit = await commit();
  assert.equal(warningCommit.result, 'created');
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  await page
    .getByRole('row')
    .filter({ hasText: '合成警告测评已确认' })
    .getByRole('button', { name: '评分概况', exact: true })
    .click();
  assert.doesNotMatch(
    await page.locator('.ant-drawer-open').innerText(),
    /合成课程作业|2026-01-15/,
  );
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
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
  await upload('normal', '合成竞赛测评');
  const normalCommit = await commit();
  assert.equal(normalCommit.result, 'created');
  await upload('normal', '合成重复上传');
  assert.equal((await commit()).result, 'duplicate');
  assert.equal((await request('/batches')).total_count, 2);
  checks.push(
    'warning acknowledgement tied to updated preview version; created/duplicate/replayed results',
  );
  checkpoint = 'statistics';
  await navigation('学情诊断');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  assert.match(await page.locator('.stat-grid').innerText(), /23/);
  assert.match(await page.locator('.stat-grid').innerText(), /参评人数/);
  await page.getByRole('heading', { name: '评分画像', exact: true }).waitFor();
  const radarBox = await page.locator('.server-chart').boundingBox();
  await page
    .locator('.server-chart')
    .hover({ position: { x: radarBox.width / 2, y: radarBox.height * 0.53 } });
  await page.locator('.server-chart').getByText('测评平均分', { exact: false }).first().waitFor();
  await page.mouse.move(0, 0);
  assert.equal(await page.getByText(/^课程：/).count(), 0);
  assert.doesNotMatch(await page.locator('.workspace').innerText(), /班级|全班|课堂成绩|算法比赛/);
  await page.locator('.insights-right .live-candidate').waitFor();
  for (const dimension of [
    '算法与数据结构',
    '代码实现能力',
    '问题建模能力',
    '效率意识',
    '解题策略',
  ]) {
    await selectAspect(dimension);
    assert.equal(await page.locator('.insights-right .live-candidate').count(), 1);
    await page
      .locator('.insights-right .live-candidate h2')
      .filter({ hasText: dimension })
      .waitFor();
    const summaryMetricCount = await page.locator('.insights-right .live-metrics button').count();
    assert.ok(summaryMetricCount > 0 && summaryMetricCount <= 4);
  }
  await selectAspect('算法与数据结构');
  assert.equal(
    await page
      .locator('.insights-statistics-panel')
      .evaluate((element) => getComputedStyle(element).overflowY),
    'visible',
  );
  const overviewBounds = await page.locator('.radar-panel').boundingBox();
  const detailsBounds = await page.locator('.insights-right').boundingBox();
  assert.ok(
    detailsBounds.height <= overviewBounds.height + 220,
    'A single aspect must not leave a long empty left column',
  );
  await shot('insights-desktop');
  await selectBatch('合成警告测评已确认');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  await selectBatch('合成竞赛测评');
  await page.getByRole('img', { name: /算法与数据结构均值3.11/ }).waitFor();
  checkpoint = 'group pagination';
  await navigation('画像探索');
  await page.locator('.match-summary').filter({ hasText: '23 / 23' }).waitFor();
  await page.locator('.sparse-matrix').waitFor();
  const matrixBox = await page.locator('.sparse-matrix').boundingBox();
  await page.mouse.move(
    matrixBox.x + 50 + ((2.75 - 1) / 4) * (matrixBox.width - 74),
    matrixBox.y + 30 + ((5 - 4) / 4) * (matrixBox.height - 125),
  );
  await page
    .locator('.sparse-matrix')
    .getByText('参评人数 12 人', { exact: false })
    .first()
    .waitFor();
  await page.mouse.move(0, 0);
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await page.getByRole('combobox', { name: '筛选条件1比较' }).click();
  await page.locator('.ant-select-dropdown').getByText('=', { exact: true }).click();
  await page.getByRole('textbox', { name: '筛选条件1评分' }).fill('2.751');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.getByText('评分阈值须为 1–5，最多两位有效小数；原输入不会自动舍入。').waitFor();
  assert.equal(await page.getByRole('textbox', { name: '筛选条件1评分' }).inputValue(), '2.751');
  await page.getByRole('textbox', { name: '筛选条件1评分' }).fill('2.75');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '12 / 23' }).waitFor();
  await page.locator('.ant-table-wrapper').getByTitle('2', { exact: true }).click();
  await page.waitForFunction(
    () => document.querySelectorAll('.ant-table-tbody tr[data-row-key]').length === 2,
  );
  assert.match(await page.locator('.match-summary').innerText(), /12 \/ 23/);
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await page.getByRole('textbox', { name: '筛选条件2评分' }).fill('3.25');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '12 / 23' }).waitFor();
  checkpoint = 'student source';
  const sourceResponse = page.waitForResponse((r) => /\/evidence\//.test(r.url()));
  await page
    .getByRole('button', { name: /查看 .* 的测评详情/ })
    .first()
    .click();
  await page.getByText('原始评语暂未开放（非空，待审核）', { exact: true }).first().waitFor();
  assert.equal(await page.locator('.ant-drawer-open .source-details').count(), 5);
  assert.equal(
    await page
      .locator('.ant-drawer-open')
      .getByText(/^课程：/)
      .count(),
    0,
  );
  await page
    .locator('.ant-drawer-open .assessment-context')
    .getByText('合成竞赛测评', { exact: true })
    .waitFor();
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  await page
    .getByRole('button', { name: /算法与数据结构 2.75 分，查看评分依据/ })
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
    .getByRole('button', { name: /算法与数据结构 2.75 分，查看评分依据/ })
    .first()
    .click();
  await page.getByText(publishedComment.display_text, { exact: true }).waitFor();
  await page.getByText(publishedRubric.display_text, { exact: true }).waitFor();
  await shot('published-evidence-desktop');
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  await writeReview('comment', source.evidence_id, 1, 'withheld');
  await writeReview('rubric', source.rubric_criterion_id, 1, 'withheld');
  await page
    .getByRole('button', { name: /算法与数据结构 2.75 分，查看评分依据/ })
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
  assert.doesNotMatch(await page.locator('.workspace').innerText(), /班级|全班|课堂成绩|算法比赛/);
  checks.push(
    'synthetic competition assessment uses neutral labels without class/course assumptions or invented course/date metadata',
  );
  checkpoint = 'metric query replay';
  await page.getByRole('button', { name: '查看这组评分洞察' }).click();
  await page.getByRole('radio', { name: '评分统计', exact: true }).locator('..').click();
  await page.locator('.insights-right .live-candidate').first().waitFor();
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
  await selectAspect('当前筛选群体');
  assert.equal(await page.locator('.insights-right .live-candidate').count(), 1);
  await page
    .locator('.insights-right .live-candidate h2')
    .filter({ hasText: '当前筛选群体概况' })
    .waitFor();
  await selectAspect('算法与数据结构');
  const recordsResponse = page.waitForResponse((r) => r.url().includes('/records/query'));
  await page
    .getByRole('button', { name: `${card.title} 测评平均分 查看评分依据`, exact: true })
    .click();
  const recordsHTTP = await recordsResponse,
    posted = recordsHTTP.request().postDataJSON();
  assert.deepEqual(posted.conditions, spec.conditions);
  assert.equal(posted.snapshot_id, spec.snapshot_id);
  assert.equal(posted.include_dimensions, true);
  const records = await recordsHTTP.json();
  const metricDrawer = page.getByRole('dialog', { name: '统计指标 · 评分依据', exact: true });
  await metricDrawer.getByRole('heading', { name: '统计说明', exact: true }).waitFor();
  assert.doesNotMatch(await metricDrawer.innerText(), /服务端计算|有理数分母/);
  await page
    .getByText(
      `指标来源记录数：${records.metric.source_record_count} · 参与记录总数：${records.total_count}`,
      { exact: true },
    )
    .waitFor();
  assert.equal(records.metric.value.exact.denominator, 46);
  assert.equal(records.metric.denominator, 23);
  await page.getByText('未纳入本指标核算', { exact: true }).first().waitFor();
  await shot('metric-evidence-desktop');
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  checks.push(
    'insight metric drilldown replays original conditions/snapshot/include_dimensions; rational denominator is not headcount',
  );
  checkpoint = 'teacher analysis save/review/export';
  checks.push(
    ...(await teacherAnalysisFlow({
      page,
      batch,
      spec,
      request,
      admin,
      scope,
      ownerToken: token,
      shot,
      navigation,
      switchSession: async (nextToken) => {
        token = nextToken;
        if (testMode)
          await writeFile(
            testSessionFile,
            JSON.stringify({ synthetic_only: true, access_token: token }),
          );
        await page.reload();
        await login();
        await selectBatch('合成竞赛测评');
      },
    })),
  );
  checkpoint = 'empty group and insight';
  await navigation('画像探索');
  await page.getByRole('textbox', { name: '筛选条件1评分' }).fill('5');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '0 / 23' }).waitFor();
  await page.getByText('没有匹配记录，请调整筛选条件。', { exact: true }).waitFor();
  await saveEmptyAnalysis(page, '合成空群体分析', 0, true);
  await navigation('学情诊断');
  const include = page.getByRole('checkbox', { name: '包含各维度洞察' });
  await include.uncheck();
  // Empty group candidates may be insufficient-data cards; no-candidate empty is tested with zero conditions + dimensions disabled.
  await navigation('画像探索');
  await page.getByRole('button', { name: '重置', exact: true }).click();
  await navigation('学情诊断');
  await page.getByRole('checkbox', { name: '包含各维度洞察' }).uncheck();
  await page.getByText('当前查询没有可展示的评分洞察候选。', { exact: true }).waitFor();
  await saveEmptyAnalysis(page, '合成无候选分析', 23, true);
  checks.push(
    'zero-match analysis saves correctly; no-candidate analysis exports a genuine zero-confirmed report',
  );
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
    await navigation('学情诊断');
    await page.locator('.live-error').filter({ hasText: code }).waitFor();
    assert.equal(await page.getByText('合成私密输入不得回显', { exact: true }).count(), 0);
    await page.unroute(pattern);
    await navigation('画像探索');
  }
  const statsPattern = '**/api/edu/batches/*/statistics?*';
  await page.route(statsPattern, (route) => route.abort('failed'));
  await navigation('学情诊断');
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
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  const largeRow = page.getByRole('row').filter({ hasText: '合成401评分测评' });
  await largeRow.getByRole('button', { name: '查看学情' }).click();
  await page.getByRole('radio', { name: '评分统计', exact: true }).locator('..').click();
  await page.getByRole('img', { name: /算法与数据结构均值3(?:，|$)/ }).waitFor();
  await page
    .locator('.insights-right .live-candidate')
    .getByText('评分分布', { exact: true })
    .click();
  await page.getByRole('combobox', { name: '查看评分人数', exact: true }).fill('5.00');
  await page.locator('.ant-select-dropdown:visible').getByText('5 分', { exact: true }).click();
  assert.equal(await page.locator('.live-metrics button').count(), 1);
  const frequencyEvidence = page.waitForResponse((r) => r.url().includes('/records/query'));
  await page.locator('.live-metrics button').click();
  const frequencyHTTP = await frequencyEvidence;
  assert.equal(frequencyHTTP.request().postDataJSON().metric_key, 'frequency:5');
  assert.equal((await frequencyHTTP.json()).metric.at_score.value, 5);
  await page.locator('.ant-drawer-open .ant-drawer-close').click();
  assert.ok(
    (await page.locator('.live-metrics button').count()) < 80,
    'Progressively disclose 401 score buckets',
  );
  await navigation('画像探索');
  await page.getByRole('img', { name: '实际评分组合，401 个实际评分组合，配对人数 401' }).waitFor();
  assert.ok(
    (await page.locator('.sparse-matrix svg path').count()) < 1000,
    'No Cartesian expansion',
  );
  const axisLabels = await page.locator('.sparse-matrix svg text').allTextContents();
  assert.ok(axisLabels.includes('1') && axisLabels.includes('5'), 'Readable 1–5 score axes');
  await shot('401-sparse-scores');
  checks.push('401 distinct legal scores rendered as 401 sparse cells without full-grid expansion');
  checks.push(
    'one selected insight aspect at a time, no inner panel scrolling; exact score dropdown and evidence remain bounded for 401 score buckets',
  );
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
  await navigation('学情诊断');
  await requested;
  await selectBatch('合成竞赛测评');
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
    ['学情诊断', 'insights-mobile'],
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
  await page.locator('input[type=file]').setInputFiles(path.join(temporary, 'normal.xlsx'));
  await page.getByText('当前教师没有执行此操作的权限。', { exact: true }).waitFor();
  admin('token-revoke', '--teacher-id', teacher);
  const workspaceExpiredHTTP = page.waitForResponse(
    (r) => r.url().includes('/api/edu/') && r.status() === 401,
  );
  workspaceExpiredHTTP.catch(() => {});
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  assert.equal((await workspaceExpiredHTTP).status(), 401);
  await page
    .getByRole('heading', { name: testMode ? '正在连接数据' : '连接账户，查看测评' })
    .waitFor();
  assert.equal(await page.locator('.live-workspace').count(), 0);
  checks.push(
    'actual server 403 after import-permission revocation; actual 401 clears teacher access and all formal data',
  );
  checkpoint = 'refresh and persistence';
  token = admin('token-issue', '--teacher-id', teacher, '--hours', '1').access_token;
  if (testMode)
    await writeFile(testSessionFile, JSON.stringify({ synthetic_only: true, access_token: token }));
  await login(true);
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  await page.getByRole('row').filter({ hasText: '合成竞赛测评' }).waitFor();
  const storage = await page.evaluate(() => ({
    local: JSON.stringify(localStorage),
    session: JSON.stringify(sessionStorage),
    cookies: document.cookie,
    url: location.href,
    html: document.documentElement.outerHTML,
  }));
  assert.ok(Object.values(storage).every((value) => !value.includes(token)));
  await page.reload();
  if (!testMode) await page.getByRole('heading', { name: '连接账户，查看测评' }).waitFor();
  await login();
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  await page.getByRole('row').filter({ hasText: '合成竞赛测评' }).waitFor();
  await page.goto(`${origin}/dev/demo`);
  await navigation('测评数据');
  await page.getByRole('heading', { name: '上传测评资料' }).waitFor();
  await page.goto(`${origin}/dev/live`);
  if (testMode) {
    await page.locator('.live-workspace').waitFor();
    await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
    await page.getByRole('row').filter({ hasText: '合成竞赛测评' }).waitFor();
  } else await page.getByRole('heading', { name: '连接账户，查看测评' }).waitFor();
  checks.push(
    testMode
      ? 'test session auto-connects after refresh and Demo return; database batches persist; no browser token input or persistence'
      : 'DB batches survive refresh; token absent from storage/URL/DOM; Demo transition clears formal session',
  );
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(
    browserAuthorization,
    [],
    'Test-session browser requests contain no Authorization credential',
  );
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
  const resolved = path.resolve(temporary);
  assert.ok(
    resolved.startsWith(path.resolve(tmpdir()) + path.sep) &&
      path.basename(resolved).startsWith('weiran-edu-browser-'),
  );
  await rm(resolved, { recursive: true, force: true });
}
