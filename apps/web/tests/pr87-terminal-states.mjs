// Focused acceptance for PR #87 terminal-state and restore-notice changes.
// Requires the isolated backend host (serve.py) already running; starts Next itself.
import assert from 'node:assert/strict';
import { spawn, execFileSync } from 'node:child_process';
import { once } from 'node:events';
import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from 'playwright';

const web = fileURLToPath(new URL('..', import.meta.url));
const connection = JSON.parse(await readFile(process.env.EDU_AI_CONNECTION, 'utf8'));
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python);
const sessionFile = path.join(path.dirname(process.env.EDU_AI_CONNECTION), 'web-session.private.json');
await writeFile(sessionFile, JSON.stringify({ synthetic_only: true, access_token: connection.bearer_token }));

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
const database = path.join(path.dirname(process.env.EDU_AI_CONNECTION), 'synthetic.sqlite3');
const checks = [];
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
    executablePath: process.env.EDU_TEST_BROWSER,
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(30000);
  const ref = connection.batches['overlap96'];
  const storageKey = `weiran.edu.ai-task.${me.teacher_id}.${ref.batch_id}.${ref.snapshot_id}`;

  // Scenario 1: restore a missing task pointer -> neutral notice, still able to type.
  await page.goto(origin + '/insights');
  await page.evaluate(
    ({ teacher, id }) => localStorage.setItem('weiran.edu.current-batch.' + teacher, id),
    { teacher: me.teacher_id, id: ref.batch_id },
  );
  await page.evaluate(
    ({ key }) => sessionStorage.setItem(key, '00000000-0000-0000-0000-000000000000'),
    { key: storageKey },
  );
  await page.reload();
  await page.getByText('上次分析任务已失效，可重新开始。', { exact: true }).waitFor();
  const startButton = page.getByRole('button', { name: '开始分析', exact: true });
  await page.getByLabel('想了解什么？', { exact: true }).fill('有哪些值得关注的问题？');
  assert.equal(await startButton.isEnabled(), true, 'start should be enabled after restore notice');
  checks.push('restore missing task -> neutral notice + start enabled');

  // Scenario 2: start then cancel -> neutral info "已停止", not a warning.
  await page.evaluate(({ key }) => sessionStorage.removeItem(key), { key: storageKey });
  await page.reload();
  await page.getByLabel('想了解什么？', { exact: true }).waitFor();
  await page.getByLabel('想了解什么？', { exact: true }).fill('有哪些值得关注的问题？');
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  await page.getByRole('button', { name: '停止分析', exact: true }).waitFor({ timeout: 120000 });
  await page.getByRole('button', { name: '停止分析', exact: true }).click();
  const stoppedAlert = page.getByRole('alert').filter({ hasText: '已停止' });
  await stoppedAlert.waitFor({ timeout: 30000 });
  const stoppedClass = await stoppedAlert.getAttribute('class');
  assert.ok(/ant-alert-info/.test(stoppedClass ?? ''), 'cancelled should be info, got ' + stoppedClass);
  assert.equal(await page.getByText('分析失败', { exact: true }).count(), 0);
  checks.push('cancelled -> info "已停止" (not warning)');

  const taskId = await page.evaluate(({ key }) => sessionStorage.getItem(key), { key: storageKey });
  assert.ok(taskId, 'need task id for DB manipulation');

  // Scenario 3: failed -> warning "分析失败" + accurate hint (no restart promise).
  execFileSync(
    python,
    ['-c',
      'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE ai_synthetic_tasks SET state=\'failed\', error_code=\'AI_TASK_FAILED\', cancelled=0 WHERE task_id=?", (sys.argv[2],)); c.commit(); c.close()',
      database, taskId],
    { encoding: 'utf8', windowsHide: true },
  );
  await page.reload();
  await page.getByRole('alert').filter({ hasText: '分析失败' }).waitFor({ timeout: 30000 });
  await page.getByText('本轮分析未完成，可切换其他测评批次。', { exact: true }).waitFor();
  checks.push('failed -> warning "分析失败" + accurate hint');

  // Scenario 4: timed_out -> warning "分析超时" + hint.
  execFileSync(
    python,
    ['-c',
      'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("UPDATE ai_synthetic_tasks SET state=\'timed_out\', error_code=\'AI_TASK_TIMEOUT\' WHERE task_id=?", (sys.argv[2],)); c.commit(); c.close()',
      database, taskId],
    { encoding: 'utf8', windowsHide: true },
  );
  await page.reload();
  await page.getByRole('alert').filter({ hasText: '分析超时' }).waitFor({ timeout: 30000 });
  await page.getByText('本轮分析未完成，可切换其他测评批次。', { exact: true }).waitFor();
  checks.push('timed_out -> warning "分析超时" + accurate hint');

  console.log(JSON.stringify({ checks, pass: true }, null, 2));
} finally {
  if (browser) await browser.close().catch(() => {});
  frontend.kill('SIGKILL');
}
