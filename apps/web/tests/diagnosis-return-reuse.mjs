// Real production Next + FastAPI + SQLite + Chromium; never uses the demo launcher.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from 'playwright';

const here = fileURLToPath(new URL('.', import.meta.url));
const repo = path.resolve(process.env.EDU_BENCH_REPO || path.join(here, '../../..'));
const web = path.join(repo, 'apps/web');
const apiRoot = path.join(repo, 'apps/api');
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python, 'EDU_TEST_PYTHON must point to the locked Python environment');
const output = path.resolve(
  process.env.EDU_BENCH_OUTPUT || 'artifacts/diagnosis-return-reuse.json',
);
await mkdir(path.dirname(output), { recursive: true });
const temporary = await mkdtemp(path.join(tmpdir(), 'edu-return-reuse-'));
const database = path.join(temporary, 'synthetic.sqlite3');
const counter = path.join(temporary, 'worker-calls.txt');
await writeFile(counter, '');
const env = {
  ...process.env,
  PYTHONPATH: apiRoot,
  WEIRAN_EDU_ENV: 'test',
  WEIRAN_EDU_DATABASE: database,
  EDU_WORKER_COUNTER: counter,
  NEXT_TELEMETRY_DISABLED: '1',
  EDU_TEST_MODE: '0',
  EDU_AI_SYNTHETIC_MODE: '0',
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
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    assert.equal(child.exitCode, null, 'Owned service exited');
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(1500) })).ok) return;
    } catch {}
    await sleep(200);
  }
  throw Error('Owned service startup timeout');
}
const report = {
  startedAt: new Date().toISOString(),
  baseline: process.env.EDU_BENCH_LABEL,
  measurements: [],
  checks: [],
  errors: [],
  network: [],
};
let browser,
  page,
  checkpoint = 'setup';
try {
  admin('init');
  const teacher = admin('teacher-create', '--subject', 'synthetic-return-owner').teacher_id;
  const other = admin('teacher-create', '--subject', 'synthetic-return-other').teacher_id;
  const scope = admin('scope-create', '--label', '合成返回验收 A').data_scope_id;
  const scopeB = admin('scope-create', '--label', '合成返回验收 B').data_scope_id;
  admin('member-set', '--teacher-id', teacher, '--scope-id', scope, '--can-import');
  admin('member-set', '--teacher-id', other, '--scope-id', scopeB, '--can-import');
  const token = admin('token-issue', '--teacher-id', teacher, '--hours', '1').access_token;
  const tokenB = admin('token-issue', '--teacher-id', other, '--hours', '1').access_token;
  execFileSync(python, [path.join(here, 'make-discovery-fixtures.py'), temporary], {
    cwd: apiRoot,
    env,
    windowsHide: true,
  });
  const apiPort = await freePort(),
    webPort = await freePort();
  const api = `http://127.0.0.1:${apiPort}`,
    origin = `http://127.0.0.1:${webPort}`;
  const backend = service(python, [path.join(here, 'diagnosis-reuse-server.py')], apiRoot, {
    ...env,
    EDU_BENCH_PORT: String(apiPort),
  });
  await ready(api + '/api/v1/health', backend);
  async function request(route, credential, body, method = 'POST') {
    const started = performance.now();
    const response = await fetch(api + '/api/v1/edu' + route, {
      method,
      headers: {
        Authorization: 'Bearer ' + credential,
        ...(body instanceof FormData ? {} : { 'content-type': 'application/json' }),
      },
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
      signal: AbortSignal.timeout(60000),
    });
    const data = await response.json();
    report.network.push({
      transport: 'direct',
      path: route,
      status: response.status,
      ms: Math.round(performance.now() - started),
    });
    return { status: response.status, data };
  }
  async function upload(kind, credential, dataScope, title, revision) {
    checkpoint = `upload-${kind}-preview`;
    const form = new FormData();
    form.set(
      'file',
      new Blob([await readFile(path.join(temporary, kind + '.xlsx'))]),
      'synthetic.xlsx',
    );
    form.set('data_scope_id', dataScope);
    form.set('title', title);
    if (revision) form.set('revision_of_batch_id', revision);
    const preview = await request('/imports/preview', credential, form);
    assert.equal(preview.status, 201, JSON.stringify(preview.data));
    checkpoint = `upload-${kind}-commit`;
    const committed = await request(
      `/imports/${preview.data.import.import_id}/commit`,
      credential,
      { preview_version: preview.data.import.preview_version, acknowledge_warnings: true },
    );
    assert.equal(committed.status, 201, JSON.stringify(committed.data));
    return (await request('/batches/' + committed.data.batch_id, credential, undefined, 'GET'))
      .data;
  }
  const batch = await upload('contrast105', token, scope, '合成返回 A105');
  const batch2 = await upload('precision105', token, scope, '合成返回 A随机105');
  const batchB = await upload('contrast', tokenB, scopeB, '合成返回 B53');
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
    { ...env, EDU_API_ORIGIN: api, EDU_TEST_SESSION_FILE: '' },
  );
  await ready(origin, frontend);
  browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.EDU_TEST_BROWSER ||
      path.join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe'),
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(90000);
  page.on('pageerror', (e) => report.errors.push(e.message));
  page.on('requestfailed', (r) => {
    if (r.url().includes('/api/edu/')) {
      const failure = { path: new URL(r.url()).pathname, failure: r.failure()?.errorText };
      report.network.push(failure);
      console.log(JSON.stringify(failure));
    }
  });
  const starts = new Map();
  page.on('request', (r) => {
    if (r.url().includes('/api/edu/')) starts.set(r, performance.now());
  });
  page.on('requestfinished', async (r) => {
    if (!starts.has(r)) return;
    const response = await r.response();
    report.network.push({
      transport: 'proxy',
      path: new URL(r.url()).pathname,
      status: response.status(),
      ms: Math.round(performance.now() - starts.get(r)),
    });
    starts.delete(r);
  });
  const card = () => page.locator('.discoveries-panel [data-testid=discovery-card]');
  const calls = async () =>
    (await readFile(counter, 'utf8')).trim().split('\n').filter(Boolean).length;
  async function connect(credential) {
    await page.getByRole('main').getByRole('button', { name: '连接账户', exact: true }).click();
    await page.getByLabel('访问凭据', { exact: true }).fill(credential);
    // Submit through the input's existing keyboard handler, avoiding modal animation hit testing.
    await page.getByLabel('访问凭据', { exact: true }).press('Enter');
    await Promise.race([
      page.getByRole('dialog').waitFor({ state: 'hidden' }),
      page
        .locator('.ant-modal .ant-alert')
        .waitFor()
        .then(async () => {
          throw Error(
            'Connection failed: ' + (await page.locator('.ant-modal .ant-alert').innerText()),
          );
        }),
    ]);
  }
  async function nav(name) {
    await page.getByRole('link', { name, exact: true }).click();
  }
  async function select(title) {
    await page.getByRole('combobox', { name: '当前测评批次' }).click();
    await page.locator('.ant-select-item-option-content').getByText(title, { exact: true }).click();
  }
  async function measure(label, action) {
    checkpoint = label;
    const before = await calls(),
      n = report.network.length,
      started = performance.now();
    await action();
    await card().first().waitFor();
    const focusMs = Math.round(performance.now() - started);
    await page.waitForLoadState('networkidle');
    const requests = report.network
      .slice(n)
      .filter((x) => x.transport === 'proxy' && /\/discoveries\//.test(x.path));
    const row = {
      label,
      ms: Math.round(performance.now() - started),
      focusMs,
      workerExecutions: (await calls()) - before,
      discoveryRequests: requests.length,
      statuses: requests.map((x) => x.status),
      title: await card().first().locator('h3').innerText(),
    };
    assert.ok(
      requests.every((x) => x.status === 200),
      JSON.stringify(row),
    );
    report.measurements.push(row);
    console.log(JSON.stringify(row));
  }
  await page.goto(origin + '/insights');
  await measure('first', async () => {
    await connect(token);
  });
  if (process.env.EDU_BENCH_AUTH_ONLY !== '1') {
    for (let i = 1; i <= 3; i++) {
      await nav('画像探索');
      await page.waitForLoadState('networkidle');
      await measure('return-' + i, () => nav('学情诊断'));
    }
    await page.screenshot({ path: output.replace(/\.json$/, '.png'), fullPage: true });
  }
  if (process.env.EDU_BENCH_PERF_ONLY !== '1') {
    // The default selected batch is the most recently imported A随机105.
    if (process.env.EDU_BENCH_AUTH_ONLY !== '1') {
      await measure('switch-batch', () => select(batch.title));
      await nav('画像探索');
      await page.waitForLoadState('networkidle');
      await measure('return-other-batch', () => nav('学情诊断'));
      const current = await request(`/batches/${batch.batch_id}/discoveries/query`, token, {
        snapshot_id: batch.snapshot_id,
      });
      assert.equal(current.status, 200);
      const finding = current.data.items[0];
      const detail = await request(
        `/batches/${batch.batch_id}/discoveries/${finding.discovery_id}/query`,
        token,
        { snapshot_id: batch.snapshot_id },
      );
      assert.equal(detail.status, 200);
      // All sensitive reads use their existing endpoints, with no reuse session header.
      const chart = await request(
        `/batches/${batch.batch_id}/discoveries/${finding.discovery_id}/chart/query`,
        token,
        { snapshot_id: batch.snapshot_id },
      );
      assert.equal(chart.status, 200);
      const students = await request(
        `/batches/${batch.batch_id}/students`,
        token,
        undefined,
        'GET',
      );
      assert.equal(students.status, 200);
      const evidence = await request(
        `/batches/${batch.batch_id}/students/${students.data.items[0].student_id}/evidence?snapshot_id=${batch.snapshot_id}`,
        token,
        undefined,
        'GET',
      );
      assert.equal(evidence.status, 200);
      report.checks.push('real chart/student/evidence reads 200; no reuse header');
    }
    await page.getByRole('button', { name: '结束会话', exact: true }).click();
    assert.equal(await card().count(), 0);
    await measure('reconnect-same-token', () => connect(token));
    await page.getByRole('button', { name: '结束会话', exact: true }).click();
    await measure('switch-teacher', () => connect(tokenB));
    assert.equal(
      await page.getByRole('combobox', { name: '当前测评批次' }).getAttribute('aria-expanded'),
      'false',
    );
    assert.ok(!(await page.locator('body').innerText()).includes(batch.title));
    report.checks.push('logout clears cards; teacher B sees own batch only');
    await nav('画像探索');
    await page.waitForLoadState('networkidle');
    admin('member-remove', '--teacher-id', other, '--scope-id', scopeB);
    checkpoint = 'membership-revocation';
    const deniedMembership = page.waitForResponse(
      (r) =>
        r.url().includes(`/batches/${batchB.batch_id}/discoveries/query`) && r.status() === 404,
    );
    await nav('学情诊断');
    assert.equal((await deniedMembership).status(), 404);
    await page.waitForLoadState('networkidle');
    assert.equal(await card().count(), 0);
    report.checks.push('revoked membership: 404; zero old cards');
    admin('member-set', '--teacher-id', other, '--scope-id', scopeB, '--can-import');
    await page.getByRole('button', { name: '结束会话', exact: true }).click();
    await measure('reconnect-after-membership-restore', () => connect(tokenB));
    await nav('画像探索');
    await page.waitForLoadState('networkidle');
    admin('token-revoke', '--teacher-id', other);
    const deniedToken = page.waitForResponse(
      (r) => r.url().includes('/api/edu/') && r.status() === 401,
    );
    await nav('学情诊断');
    assert.equal((await deniedToken).status(), 401);
    await page.waitForLoadState('networkidle');
    assert.equal(await card().count(), 0);
    report.checks.push('token revoked: 401 and session cleared, zero old cards');
    await measure('owner-reconnect', () => connect(token));
    // A real revision appends a new snapshot; authorized history remains legal.
    await select(batch2.title);
    await card().first().waitFor();
    await page.waitForLoadState('networkidle');
    await nav('画像探索');
    await page.waitForLoadState('networkidle');
    const revised = await upload('wide', token, scope, '合成新快照53', batch2.batch_id);
    checkpoint = 'snapshot-revision';
    await measure('authorized-historical-snapshot', () => nav('学情诊断'));
    const old = await request(`/batches/${batch2.batch_id}/discoveries/query`, token, {
      snapshot_id: batch2.snapshot_id,
    });
    assert.equal(old.status, 200);
    const wrong = await request(`/batches/${batch2.batch_id}/discoveries/query`, token, {
      snapshot_id: revised.snapshot_id,
    });
    assert.equal(wrong.status, 409);
    await page.reload();
    await measure('new-snapshot', async () => {
      await connect(token);
      await select(revised.title);
    });
    report.checks.push(
      'real revision: new batch/snapshot computes independently; mismatched snapshot 409; authorized historical snapshot remains legal',
    );
    assert.ok(revised.snapshot_id !== batch2.snapshot_id);
  }
  assert.deepEqual(report.errors, []);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = { checkpoint, message: String(error) };
  if (page)
    await page
      .screenshot({ path: output.replace(/\.json$/, '-failure.png'), fullPage: true })
      .catch(() => {});
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  await browser?.close();
  for (const child of children.reverse()) if (child.exitCode === null) child.kill();
  await rm(temporary, { recursive: true, force: true, maxRetries: 4, retryDelay: 300 });
  console.log(JSON.stringify({ passed: report.passed, failure: report.failure, output }));
}
