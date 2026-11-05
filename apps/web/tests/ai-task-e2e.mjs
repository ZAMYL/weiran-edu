// Requires the isolated host from AI_SYNTHETIC_FRONTEND_HANDOFF.md. Never calls Qwen.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { readFile, writeFile, mkdir, unlink } from 'node:fs/promises';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from 'playwright';
import { verifyReportArtifact } from './report-artifact-utils.mjs';

const web = fileURLToPath(new URL('..', import.meta.url));
const connection = JSON.parse(await readFile(process.env.EDU_AI_CONNECTION, 'utf8'));
assert.equal(connection.synthetic_only, true);
assert.equal(connection.execution_origin, 'http_mock');
assert.equal(connection.real_qwen, 'not_run');
assert.equal(connection.production_enabled, false);
assert.equal(new URL(connection.api_origin).hostname, '127.0.0.1');
const sessionFile = path.join(
  path.dirname(process.env.EDU_AI_CONNECTION),
  'web-session.private.json',
);
await writeFile(
  sessionFile,
  JSON.stringify({ synthetic_only: true, access_token: connection.bearer_token }),
);
const artifacts = path.join(web, 'artifacts', 'ai-task');
await mkdir(artifacts, { recursive: true });
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python);
const server = createServer();
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const port = server.address().port;
await new Promise((resolve) => server.close(resolve));
const origin = 'http://127.0.0.1:' + port;
const env = {
  ...process.env,
  EDU_TEST_MODE: '1',
  EDU_AI_SYNTHETIC_MODE: '1',
  EDU_TEST_SESSION_FILE: sessionFile,
  EDU_API_ORIGIN: connection.api_origin,
  NEXT_TELEMETRY_DISABLED: '1',
};
const frontend = spawn(
  process.execPath,
  ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)],
  { cwd: web, env, stdio: 'ignore', windowsHide: true },
);
const checks = [],
  pageErrors = [],
  requests = [];
let browser, page;
try {
  for (let attempt = 0; ; attempt++) {
    assert.equal(frontend.exitCode, null);
    assert.ok(attempt < 100, 'Next startup timeout');
    const response = await fetch(origin, { signal: AbortSignal.timeout(1000) }).catch(() => null);
    if (response?.ok) break;
    await sleep(200);
  }
  const me = await (await fetch(origin + '/api/edu/me')).json();
  browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.EDU_TEST_BROWSER ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(25000);
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => {
    if (request.url().includes('/api/edu/'))
      assert.equal(request.headers().authorization, undefined);
    assert.ok(!/qwen|dashscope/i.test(request.url()));
  });
  page.on('response', (response) => {
    if (response.url().includes('/ai-investigation-tasks'))
      requests.push({
        method: response.request().method(),
        status: response.status(),
        path: new URL(response.url()).pathname.replace(/[0-9a-f-]{36}/g, '{id}'),
      });
  });
  async function openCase(name) {
    const ref = connection.batches[name];
    await page.goto(origin + '/insights');
    await page.evaluate(
      ({ teacher, id }) => localStorage.setItem('weiran.edu.current-batch.' + teacher, id),
      { teacher: me.teacher_id, id: ref.batch_id },
    );
    await page.reload();
    await page.getByRole('heading', { name: 'AI 辅助分析', exact: true }).waitFor();
    await page.getByLabel('想了解什么？', { exact: true }).waitFor();
    return ref;
  }
  const normal = await openCase('normal');
  const unsupported = page.waitForResponse(
    (r) => r.url().endsWith('/ai-investigation-tasks') && r.status() === 422,
  );
  await page.getByLabel('想了解什么？', { exact: true }).fill('有哪些值得关注的问题？');
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  assert.equal((await (await unsupported).json()).error.code, 'AI_GOAL_UNSUPPORTED');
  await page.getByText(/当前合成服务不支持这个问题/).waitFor();
  checks.push('literal original question receives real unsupported-goal 422');
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/ai-investigation-tasks') && r.status() === 202,
  );
  await page.getByLabel('想了解什么？', { exact: true }).fill(normal.question);
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  const start = await (await created).json();
  assert.equal(start.execution_origin, 'http_mock');
  await page.getByTestId('ai-fact-card').first().waitFor({ timeout: 240000 });
  await page.getByText('分析已结束', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, 'normal-facts.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(artifacts, 'normal-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const cards = page.getByTestId('ai-fact-card');
  assert.ok((await cards.count()) > 0);
  await cards.first().getByRole('button', { name: '查看依据', exact: true }).click();
  const sources = page.getByRole('dialog', { name: '分析依据', exact: true });
  await sources.getByRole('button', { name: '查看原评分依据', exact: true }).first().click();
  await page.getByText('原始数值：', { exact: false }).waitFor();
  await page.waitForFunction(
    () =>
      document.querySelectorAll('.ant-drawer-content-wrapper').length > 0 &&
      Array.from(document.querySelectorAll('.ant-drawer-content-wrapper')).every((drawer) =>
        drawer
          .getAnimations({ subtree: true })
          .every((animation) => animation.playState === 'finished'),
      ),
  );
  await page.screenshot({ path: path.join(artifacts, 'normal-source.png'), fullPage: true });
  await page.locator('.ant-drawer-open').last().locator('.ant-drawer-close').click();
  await sources.locator('.ant-drawer-close').click();
  const explore = cards.getByRole('button', { name: '继续探索', exact: true }).first();
  if (await explore.count()) {
    await explore.click();
    await page.getByText('应用筛选', { exact: true }).waitFor();
    await page.getByRole('link', { name: '学情诊断', exact: true }).click();
    await page.getByTestId('ai-fact-card').first().waitFor();
  }
  for (const checkbox of await cards
    .getByRole('checkbox', { name: '加入待核查', exact: true })
    .all())
    await checkbox.check();
  const reviewResponse = page.waitForResponse(
    (r) => r.url().endsWith('/review-submissions') && r.status() === 200,
  );
  await page.getByRole('button', { name: /提交待核查/ }).click();
  const receipt = await (await reviewResponse).json();
  assert.equal(receipt.status, 'pending_teacher_review');
  const review = page.getByRole('region', { name: '分析核验', exact: true });
  await review.waitFor();
  for (const finding of await review.locator('.review-finding').all()) {
    if ((await finding.getAttribute('open')) === null)
      await finding.locator(':scope > summary').click();
    await finding
      .getByRole('radio', { name: '已确认', exact: true })
      .locator('xpath=ancestor::label[1]')
      .click();
  }
  const patched = page.waitForResponse(
    (r) => r.url().endsWith('/' + receipt.result_id) && r.request().method() === 'PATCH',
  );
  await review.getByRole('button', { name: '保存核验结果', exact: true }).click();
  const confirmed = await (await patched).json();
  assert.equal(confirmed.version, 2);
  assert.ok(
    confirmed.discovery_findings.every((finding) => finding.teacher_review.status === 'confirmed'),
  );
  await review.getByText('已保存版本 2', { exact: true }).waitFor();
  await page.screenshot({
    path: path.join(artifacts, 'normal-reviewed-report.png'),
    fullPage: true,
  });
  for (const format of ['xlsx', 'md']) {
    const preflight = page.waitForResponse(
      (r) => r.url().endsWith('/export-check') && r.request().postDataJSON().format === format,
    );
    await review.getByRole('button', { name: '导出测评报告', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '导出测评报告', exact: true });
    if (format === 'md') await dialog.getByText('Markdown', { exact: true }).click();
    await preflight;
    const ack = dialog.getByRole('checkbox');
    if (await ack.count()) await ack.check();
    const download = page.waitForEvent('download');
    await dialog
      .getByRole('button', { name: format === 'md' ? '下载 Markdown' : '下载 XLSX', exact: true })
      .click();
    const file = await download;
    const bytes = await readFile(await file.path());
    const destination = path.join(artifacts, 'ai-confirmed.' + format);
    await writeFile(destination, bytes);
    verifyReportArtifact({
      format,
      bytes,
      destination,
      python,
      env: process.env,
      reference: {
        studentCount: 53,
        discoveryIds: receipt.candidate_ids,
        batchId: normal.batch_id,
        snapshotId: normal.snapshot_id,
        resultId: receipt.result_id,
        version: 2,
      },
    });
    await dialog.locator('.ant-modal-close').click();
  }
  checks.push(
    '202 creation / real task polling / verified facts / original evidence / exploration / pending / teacher confirmation / original MD and XLSX reopened',
  );
  for (const name of ['empty', 'evidence']) {
    const ref = await openCase(name);
    await page.getByLabel('想了解什么？', { exact: true }).fill(ref.question);
    await page.getByRole('button', { name: '开始分析', exact: true }).click();
    await page.getByText('分析已结束', { exact: true }).waitFor({ timeout: 240000 });
    if (name === 'empty') {
      await page.getByText('本轮未发现符合条件的结果', { exact: true }).waitFor();
      assert.equal(await cards.count(), 0);
      assert.equal(await page.getByRole('button', { name: /提交待核查/ }).count(), 0);
    } else {
      await page.getByText('进一步解释需要补充资料', { exact: true }).waitFor();
      assert.ok((await cards.count()) > 0);
    }
    await page.screenshot({ path: path.join(artifacts, name + '.png'), fullPage: true });
    checks.push(
      name === 'empty'
        ? 'real zero-finding task has no fabricated cards'
        : 'evidence gaps retain verified observations without invented explanations',
    );
  }
  await openCase('normal');
  await cards.first().waitFor();
  const database = path.join(path.dirname(process.env.EDU_AI_CONNECTION), 'synthetic.sqlite3');
  execFileSync(
    python,
    [
      '-c',
      [
        'import sys',
        'from pathlib import Path',
        'from app.storage.database import Database',
        'from app.edu.auth import TeacherAdmin',
        'database=Database(Path(sys.argv[1]))',
        'with database.transaction() as c: row=c.execute("SELECT owner_teacher_id,data_scope_id FROM batches WHERE batch_id=?",(sys.argv[2],)).fetchone()',
        'TeacherAdmin(database).remove_member(row[0],row[1])',
      ].join('\n'),
      database,
      normal.batch_id,
    ],
    {
      env: { ...process.env, PYTHONPATH: path.join(process.env.EDU_AI_API_ROOT, 'apps/api') },
      windowsHide: true,
    },
  );
  const denied = page.waitForResponse(
    (r) => r.url().endsWith('/records/query') && [403, 404].includes(r.status()),
  );
  await cards.first().getByRole('button', { name: '查看依据', exact: true }).click();
  const permissionResponse = await denied;
  await page
    .getByRole('dialog', { name: '分析依据', exact: true })
    .getByText(/没有执行此操作的权限|不存在或当前教师无权查看/)
    .waitFor();
  await page.screenshot({ path: path.join(artifacts, 'permission-denied.png'), fullPage: true });
  checks.push(
    'actual source membership revocation returns ' +
      permissionResponse.status() +
      ' and displays permission failure',
  );
  assert.deepEqual(pageErrors, []);
  await writeFile(
    path.join(artifacts, 'acceptance.json'),
    JSON.stringify(
      {
        frontend_revision: process.env.EDU_TEST_FRONTEND_REVISION ?? null,
        backend_revision: '0c73569f3159ba499d89f60ffda415da8e46072c',
        status: 'complete',
        execution_origin: 'http_mock',
        real_qwen: 'not_run',
        production_enabled: false,
        checks,
        requests,
        page_errors: pageErrors,
        receipt,
      },
      null,
      2,
    ),
  );
  console.log(JSON.stringify({ checks, page_errors: pageErrors }));
} catch (error) {
  if (page) await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true });
  throw error;
} finally {
  await browser?.close();
  if (frontend.exitCode === null) {
    const exited = once(frontend, 'exit');
    frontend.kill();
    await exited;
  }
  await unlink(sessionFile).catch(() => {});
}
