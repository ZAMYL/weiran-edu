// Real FastAPI + Next production proxy + Chromium, with disposable synthetic data only.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from 'playwright';
import { teacherProductFlow, credentialRelayFlow } from './teacher-product-flow.mjs';

const web = fileURLToPath(new URL('..', import.meta.url));
const repo = path.resolve(web, '../..');
const apiRoot = process.env.EDU_TEST_API_ROOT || path.join(repo, 'apps/api');
const python =
  process.env.EDU_TEST_PYTHON ||
  path.join(
    repo,
    '.local/api-venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );
assert.ok(existsSync(python), 'Set EDU_TEST_PYTHON to a locked backend Python environment.');
const temporary = await mkdtemp(path.join(tmpdir(), 'weiran-discovery-flow-'));
const database = path.join(temporary, 'synthetic.sqlite3');
const credentialFile = path.join(temporary, 'test-session.private.json');
const artifacts = path.join(
  web,
  process.env.EDU_AUTH_RELAY_ONLY === '1'
    ? 'artifacts/auth-relay'
    : process.env.EDU_PRODUCT_FLOW === '1'
      ? 'artifacts/teacher-product'
      : 'artifacts/discovery-flow',
);
await mkdir(artifacts, { recursive: true });
const env = {
  ...process.env,
  PYTHONPATH: apiRoot,
  WEIRAN_EDU_ENV: 'test',
  WEIRAN_EDU_DATABASE: database,
  NEXT_TELEMETRY_DISABLED: '1',
};
const admin = (...args) =>
  JSON.parse(
    execFileSync(python, ['-m', 'app.edu', '--database', database, ...args], {
      cwd: apiRoot,
      env,
      encoding: 'utf8',
      windowsHide: true,
    }),
  );
async function freePort() {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}
const children = [];
function service(bin, args, cwd, environment) {
  const child = spawn(bin, args, { cwd, env: environment, stdio: 'ignore', windowsHide: true });
  children.push(child);
  return child;
}
async function ready(url, child) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    assert.equal(child.exitCode, null, 'Synthetic service exited during startup');
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(1500) })).ok) return;
    } catch {
      /* readiness only */
    }
    await sleep(200);
  }
  throw new Error('Synthetic service startup timeout');
}
let browser, page, token, origin;
let checkpoint = 'setup';
const checks = [],
  errors = [],
  browserCredentials = [],
  modelRequests = [];
const responses = [];
const consoleErrors = [];
try {
  admin('init');
  const teacher = admin('teacher-create', '--subject', 'synthetic-discovery-owner').teacher_id;
  const reader = admin('teacher-create', '--subject', 'synthetic-discovery-reader').teacher_id;
  const scope = admin('scope-create', '--label', '合成发现验收来源').data_scope_id;
  admin('member-set', '--teacher-id', teacher, '--scope-id', scope, '--can-import');
  admin('member-set', '--teacher-id', reader, '--scope-id', scope);
  token = admin('token-issue', '--teacher-id', teacher, '--hours', '1').access_token;
  const readerToken = admin('token-issue', '--teacher-id', reader, '--hours', '1').access_token;
  const setToken = (value) =>
    writeFile(credentialFile, JSON.stringify({ synthetic_only: true, access_token: value }));
  await setToken(token);
  execFileSync(python, [path.join(web, 'tests/make-discovery-fixtures.py'), temporary], {
    cwd: apiRoot,
    env,
    windowsHide: true,
  });
  const apiPort = await freePort(),
    webPort = await freePort();
  const api = `http://127.0.0.1:${apiPort}`;
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
  assert.ok(openapi.paths['/api/v1/edu/batches/{batch_id}/discoveries/{discovery_id}/chart/query']);
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
    { ...env, EDU_API_ORIGIN: api, EDU_TEST_MODE: '1', EDU_TEST_SESSION_FILE: credentialFile },
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
  page.setDefaultTimeout(25_000);
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('request', (request) => {
    if (request.url().includes('/api/edu/') && request.headers().authorization)
      browserCredentials.push(true);
    if (/qwen|dashscope/i.test(request.url())) modelRequests.push(true);
  });
  page.on('response', (r) => {
    if (r.url().includes('/discoveries/')) responses.push(r);
  });
  const shot = (name) =>
    page.screenshot({
      path: path.join(artifacts, `${name}.png`),
      fullPage: page.viewportSize().width <= 480,
      animations: 'disabled',
    });
  const navigation = (name) => page.getByRole('link', { name, exact: true }).click();
  const panel = () =>
    page
      .locator('.panel')
      .filter({ has: page.getByRole('heading', { name: '主动学情发现', exact: true }) })
      .first();
  const waitResponse = (predicate) => {
    const pending = page.waitForResponse(predicate);
    // Keep failure observed while an associated locator action is still waiting;
    // awaiting the same promise below still raises and runs our finally cleanup.
    pending.catch(() => {});
    return pending;
  };
  const nextResponse = (suffix, status = 200) =>
    waitResponse(
      (r) =>
        r.url().includes('/discoveries/') &&
        new URL(r.url()).pathname.endsWith(suffix) &&
        r.status() === status,
    );
  const post = (route, body) =>
    page.evaluate(
      async ({ route, body }) => {
        const r = await fetch(`/api/edu${route}`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
          cache: 'no-store',
        });
        return {
          status: r.status,
          body: await r.json(),
          requestId: r.headers.get('x-request-id'),
          cache: r.headers.get('cache-control'),
        };
      },
      { route, body },
    );
  const drawers = () => page.locator('.ant-drawer-open');
  const closeTop = async () => {
    await drawers().last().locator('.ant-drawer-close').click();
    await sleep(350);
  };
  async function importThroughPage(kind, title) {
    await navigation('测评数据');
    const previewReady = waitResponse(
      (r) => r.url().includes('/imports/preview') && r.status() === 201,
    );
    await page.locator('input[type=file]').setInputFiles(path.join(temporary, `${kind}.xlsx`));
    await page.getByLabel('测评名称', { exact: true }).fill(title);
    await page.getByRole('button', { name: '下一步：预览校验', exact: true }).click();
    const preview = await (await previewReady).json();
    assert.equal(preview.summary.error_count, 0);
    await page.getByRole('heading', { name: '导入预览' }).waitFor();
    await page.locator('.source-details').filter({ hasText: '预览版本' }).waitFor();
    const ack = page.getByRole('checkbox', { name: /我已查看本预览版本/ });
    if (await ack.count()) await ack.check();
    const committed = waitResponse((r) => r.url().endsWith('/commit') && r.status() === 201);
    await page.getByRole('button', { name: '确认导入', exact: true }).click();
    const batchId = (await (await committed).json()).batch_id;
    await page.getByRole('heading', { name: '上传测评' }).waitFor();
    const list = nextResponse('/discoveries/query');
    await navigation('学情洞察');
    return { batchId, data: await (await list).json() };
  }
  async function selectBatch(title) {
    const pending = nextResponse('/discoveries/query');
    await page.getByRole('combobox', { name: '当前正式测评批次' }).click();
    await page.locator('.ant-select-dropdown').getByText(title, { exact: true }).click();
    return await (await pending).json();
  }
  async function openCard(index = 0) {
    const detail = nextResponse('/query'),
      chart = nextResponse('/chart/query');
    await panel().getByRole('button', { name: '查看发现与依据', exact: true }).nth(index).click();
    await detail;
    const full = await (await chart).json();
    await page.getByTestId('discovery-chart').locator('svg').waitFor();
    return full;
  }
  async function records(button) {
    const response = nextResponse('/records/query');
    await drawers().first().getByRole('button', { name: button, exact: true }).click();
    const result = await (await response).json();
    await page.getByTestId('discovery-record-count').waitFor();
    return result;
  }
  if (process.env.EDU_AUTH_RELAY_ONLY === '1') {
    checks.push(...(await credentialRelayFlow({ page, origin, api, admin, setToken, token })));
  } else if (process.env.EDU_PRODUCT_FLOW === '1') {
    checks.push(
      ...(await teacherProductFlow({
        page,
        origin,
        python,
        apiRoot,
        env,
        artifacts,
        admin,
        scope,
        teacher,
        token,
        reader,
        readerToken,
        setToken,
        shot,
        navigation,
        post,
        importThroughPage,
        selectBatch,
        closeTop,
        panel,
      })),
    );
  } else {
    checkpoint = 'teacher session';
    await page.goto(origin);
    await page.locator('.live-workspace').waitFor();
    assert.equal(await page.locator('#teacher-access-token').count(), 0);
    checkpoint = 'Excel upload, preview, commit and discovery list';
    const wide = await importThroughPage('wide', '合成发现五维测评');
    const spec = {
      snapshot_id: wide.data.scope.snapshot_id,
      profile: 'edu.discovery.profile.v1',
      scope_conditions: [],
    };
    const mean = wide.data.scope.statistics.dimensions[0].mean.exact;
    assert.equal(mean.numerator, 619);
    assert.equal(mean.denominator, 212);
    assert.equal(wide.data.scope.scope_record_count, 53);
    assert.ok(wide.data.retained_count > 10);
    await page.getByTestId('discovery-card').first().waitFor();
    await shot('list-desktop');
    checks.push(
      'UI Excel preview/commit -> real discovery list; exact full-range mean 619/212 and 53 students',
    );
    checkpoint = 'discovery pagination';
    const paged = nextResponse('/discoveries/query');
    await panel().getByTitle('2', { exact: true }).click();
    const second = await (await paged).json();
    assert.deepEqual(second.scope, wide.data.scope);
    assert.equal(second.page.offset, 10);
    assert.equal(second.retained_count, wide.data.retained_count);
    const firstPage = nextResponse('/discoveries/query');
    await panel().getByTitle('1', { exact: true }).click();
    await firstPage;
    checks.push('discovery pagination preserves complete scope, denominators and statistics');
    checkpoint = 'detail, full chart and original group';
    const full = await openCard();
    assert.equal(full.metrics.student_denominator, 53);
    const sourceButton = full.type === 'group_difference' ? '选定群体' : '配对评分学生';
    const participants = await records(sourceButton);
    const count = participants.total_count;
    assert.ok(count > 10);
    assert.equal(participants.items.length, 10);
    const members = nextResponse('/records/query');
    await drawers().last().getByTitle('2', { exact: true }).click();
    const members2 = await (await members).json();
    assert.equal(members2.total_count, count);
    assert.deepEqual(members2.scope, participants.scope);
    assert.equal(members2.page.offset, 10);
    checkpoint = 'authorized single score evidence';
    const proofResponse = waitResponse(
      (r) => /\/evidence\/[^/?]+\?/.test(r.url()) && r.status() === 200,
    );
    await drawers()
      .last()
      .getByRole('button', { name: /评分证据$/ })
      .first()
      .click();
    const proof = await (await proofResponse).json();
    assert.equal(proof.batch_id, wide.batchId);
    assert.equal(proof.snapshot_id, spec.snapshot_id);
    assert.equal(proof.comment, null);
    assert.equal(proof.comment_status, 'withheld');
    await drawers().last().getByRole('heading', { name: /分/ }).first().waitFor();
    assert.doesNotMatch(
      await page.locator('body').innerText(),
      /合成私密姓名|未审核合成私密评语|合成私密依据/,
    );
    await shot('score-evidence');
    await closeTop();
    await closeTop();
    await closeTop();
    checks.push(
      'detail + complete chart -> kernel original members -> paginated anonymous scores -> owned evidence; private prose withheld',
    );
    checkpoint = 'unknown finding and stale snapshot via real proxy';
    const base = `/batches/${wide.batchId}/discoveries/`;
    checkpoint = 'group comparison and original membership replay';
    const completeList = await post(`${base}query`, { ...spec, limit: 50 });
    assert.equal(completeList.status, 200);
    const groupIndex = completeList.body.items.findIndex((c) => c.type === 'group_difference');
    assert.ok(groupIndex >= 0);
    const groupPage = Math.floor(groupIndex / 10) + 1;
    if (groupPage > 1) {
      const pending = nextResponse('/discoveries/query');
      await panel().getByTitle(String(groupPage), { exact: true }).click();
      await pending;
    }
    const groupChart = await openCard(groupIndex % 10);
    assert.equal(groupChart.type, 'group_difference');
    assert.equal(
      groupChart.metrics.target_count +
        groupChart.metrics.remainder_count +
        groupChart.metrics.unevaluable_count,
      53,
    );
    assert.equal((await records('选定群体')).total_count, groupChart.metrics.target_count);
    await closeTop();
    await shot('group-chart');
    await closeTop();
    checks.push(
      'group comparison uses full five-dimension chart and exact original target membership, with M+R+U=53',
    );
    checkpoint = 'unknown finding and stale snapshot via real proxy';
    for (const [route, body, status] of [
      [`${base}${'f'.repeat(24)}/query`, spec, 404],
      [`${base}query`, { ...spec, snapshot_id: 'stale-synthetic-snapshot' }, 409],
    ]) {
      const rejected = await post(route, body);
      assert.equal(rejected.status, status);
      assert.ok(rejected.requestId?.startsWith('req_'));
      assert.equal(rejected.cache, 'no-store');
      assert.ok(!rejected.body.items);
    }
    checks.push(
      'real proxy preserves unknown-finding 404 and stale-snapshot 409 with request ID/no-store',
    );
    checkpoint = 'outer scope and boundary chart';
    await navigation('画像探索');
    await page.getByRole('button', { name: '添加条件', exact: true }).click();
    await page.getByRole('combobox', { name: '正式条件1比较', exact: true }).click();
    await page.locator('.ant-select-dropdown').getByText('≤', { exact: true }).click();
    await page.getByLabel('正式条件1评分', { exact: true }).fill('3');
    await page.getByRole('button', { name: '应用筛选', exact: true }).click();
    const scopedResponse = nextResponse('/discoveries/query');
    await navigation('学情洞察');
    const scoped = await (await scopedResponse).json();
    assert.equal(scoped.scope.scope_record_count, 40);
    assert.equal(scoped.scope.scope_conditions.length, 1);
    const boundary = await post(`${base}query`, {
      ...spec,
      scope_conditions: scoped.scope.scope_conditions,
      limit: 50,
    });
    assert.equal(boundary.status, 200);
    const boundaryIndex = boundary.body.items.findIndex((c) => c.type === 'boundary_sensitivity');
    assert.ok(boundaryIndex >= 0);
    const boundaryPage = Math.floor(boundaryIndex / 10) + 1;
    if (boundaryPage > 1) {
      const pending = nextResponse('/discoveries/query');
      await panel().getByTitle(String(boundaryPage), { exact: true }).click();
      await pending;
    }
    const boundaryChart = await openCard(boundaryIndex % 10);
    assert.equal(boundaryChart.type, 'boundary_sensitivity');
    assert.equal(boundaryChart.metrics.student_denominator, 40);
    assert.equal(boundaryChart.metrics.added_count, 13);
    const added = await records('边界新增学生');
    assert.equal(added.total_count, 13);
    assert.ok(added.items.every((s) => s.scores.every((v) => v.score === 3)));
    await closeTop();
    await closeTop();
    checks.push(
      'outer <=3 scope stays separate from finding conditions; boundary 13 added students from full scoped N=40',
    );
    checkpoint = 'rare combination reference and membership';
    const rare = await importThroughPage('rare', '合成少见组合测评');
    const rareIndex = rare.data.items.findIndex((c) => c.type === 'rare_combination');
    assert.ok(rareIndex >= 0);
    const rareChart = await openCard(rareIndex);
    assert.equal(rareChart.type, 'rare_combination');
    assert.equal(rareChart.metrics.match_count, 1);
    assert.equal(rareChart.metrics.pair_count, 17);
    assert.equal(
      rareChart.chart.reduce((n, p) => n + p.count, 0),
      17,
    );
    assert.equal((await records('少见组合学生')).total_count, 1);
    await closeTop();
    assert.equal((await records('完整配对参考学生')).total_count, 17);
    await closeTop();
    await shot('rare-chart');
    await closeTop();
    checks.push(
      'rare original members=1 vs complete pair reference=17; full distribution count=17',
    );
    checkpoint = 'empty findings';
    const empty = await importThroughPage('empty', '合成空发现测评');
    assert.deepEqual(empty.data.items, []);
    await page.getByText('当前范围没有符合条件的发现，可调整筛选后重试。').waitFor();
    checks.push('empty findings displayed as valid empty state');
    checkpoint = 'read-only grant and revocation';
    admin('batch-grant-read', '--teacher-id', reader, '--batch-id', wide.batchId);
    await setToken(readerToken);
    await page.reload();
    await page.locator('.live-workspace').waitFor();
    await page.getByTestId('discovery-card').first().waitFor();
    await openCard();
    await records('配对评分学生');
    await closeTop();
    await closeTop();
    admin('batch-revoke-read', '--teacher-id', reader, '--batch-id', wide.batchId);
    const denied = nextResponse('/discoveries/query', 404);
    await page.getByRole('button', { name: '刷新发现', exact: true }).click();
    await denied;
    await panel()
      .getByText('记录不存在或当前教师无权查看，请刷新批次。', { exact: true })
      .waitFor();
    assert.equal(await page.getByTestId('discovery-card').count(), 0);
    checks.push(
      'read-only grant reads discoveries; actual revoked grant returns 404 and clears previous cards',
    );
    checkpoint = 'expired teacher session';
    admin('token-revoke', '--teacher-id', reader);
    const expired = nextResponse('/discoveries/query', 401);
    await page.getByRole('button', { name: '刷新发现', exact: true }).click();
    await expired;
    await page.getByRole('heading', { name: '连接测试数据', exact: true }).waitFor();
    assert.equal(await page.locator('.live-workspace').count(), 0);
    checks.push('actual token revocation 401 clears the entire formal workspace');
    checkpoint = 'mobile and credential isolation';
    await setToken(token);
    await page.getByRole('button', { name: '重新连接测试数据', exact: true }).click();
    await page.locator('.live-workspace').waitFor();
    await selectBatch('合成发现五维测评');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByTestId('discovery-card').first().waitFor();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await shot('list-mobile');
    await openCard();
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await shot('detail-mobile');
    const persisted = await page.evaluate(() => [
      JSON.stringify(localStorage),
      JSON.stringify(sessionStorage),
      document.cookie,
      location.href,
      document.documentElement.outerHTML,
    ]);
    assert.ok(persisted.every((v) => !v.includes(token) && !v.includes(readerToken)));
    assert.deepEqual(browserCredentials, []);
    assert.deepEqual(modelRequests, []);
    assert.deepEqual(errors, []);
    checks.push(
      '390px list/detail without document overflow; no browser Authorization/storage tokens or remote model requests',
    );
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(modelRequests, []);
  assert.deepEqual(browserCredentials, []);
  const unexpectedConsoleErrors = consoleErrors.filter(
    (text) =>
      !/^Failed to load resource: the server responded with a status of (401|403|404|409|410|413|415|422|503)\b/.test(
        text,
      ),
  );
  assert.deepEqual(unexpectedConsoleErrors, []);
  const successCount = responses.filter((r) => r.status() === 200).length;
  await writeFile(
    path.join(artifacts, 'result.json'),
    JSON.stringify(
      {
        checks,
        pageErrors: errors,
        consoleErrors,
        unexpectedConsoleErrors,
        discoveryResponses: responses.map((r) => ({
          status: r.status(),
          path: new URL(r.url()).pathname
            .replace(/batches\/[^/]+/g, 'batches/{batch_id}')
            .replace(/discoveries\/[a-f0-9]{24}/g, 'discoveries/{discovery_id}'),
        })),
        successCount,
      },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ checks, pageErrors: errors, successCount }, null, 2));
} catch (error) {
  if (page)
    await page
      .screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true })
      .catch(() => {});
  console.error(`Discovery integration failed at: ${checkpoint}`);
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
  // Resolve and verify our generated directory before recursive cleanup.
  const resolved = path.resolve(temporary),
    allowed = path.resolve(tmpdir()) + path.sep;
  assert.ok(
    resolved.startsWith(allowed) && path.basename(resolved).startsWith('weiran-discovery-flow-'),
  );
  await rm(resolved, { recursive: true, force: true });
}
