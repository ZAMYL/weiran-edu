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
assert.equal(connection.synthetic_only, true);
assert.equal(connection.execution_origin, 'http_mock');
assert.equal(connection.real_qwen, 'not_run');
assert.equal(connection.production_enabled, false);
assert.equal(new URL(connection.api_origin).hostname, '127.0.0.1');
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python);
const sessionFile = path.join(
  path.dirname(process.env.EDU_AI_CONNECTION),
  'web-session.private.json',
);
await writeFile(
  sessionFile,
  JSON.stringify({ synthetic_only: true, access_token: connection.bearer_token }),
);

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
  const panel = page.locator('.ai-investigation-panel');
  const startButton = panel.getByRole('button', { name: '开始分析', exact: true });
  const question = panel.getByLabel('想了解什么？', { exact: true });
  const notice = panel.getByText('暂时无法读取上次分析任务。', { exact: true });
  const collectionPath = `/api/edu/batches/${ref.batch_id}/ai-investigation-tasks`;
  const pointer = () => page.evaluate((key) => sessionStorage.getItem(key), storageKey);
  const waitForTaskResponse = (method, status, pathname) =>
    page.waitForResponse(
      (r) =>
        r.request().method() === method &&
        r.status() === status &&
        new URL(r.url()).pathname === pathname,
    );
  const assertNoStart = async () => {
    assert.equal(await startButton.count(), 0, 'terminal task must not show a new start button');
    assert.equal(await question.count(), 0, 'terminal task must not show a new question field');
  };

  // A missing pointer says nothing about the campaign's availability.
  await page.goto(origin + '/insights');
  await page.evaluate(
    ({ teacher, id }) => localStorage.setItem('weiran.edu.current-batch.' + teacher, id),
    { teacher: me.teacher_id, id: ref.batch_id },
  );
  const missingTask = '00000000-0000-0000-0000-000000000000';
  await page.evaluate(({ key, id }) => sessionStorage.setItem(key, id), {
    key: storageKey,
    id: missingTask,
  });
  await Promise.all([
    waitForTaskResponse('GET', 404, `${collectionPath}/${missingTask}`),
    page.reload(),
  ]);
  await notice.waitFor();
  assert.equal(await pointer(), null);
  checks.push('404 restore clears the pointer and makes no promise about starting again');

  await panel.locator('.ai-custom-question summary').click();
  await question.fill('有哪些值得关注的问题？');
  await Promise.all([waitForTaskResponse('POST', 202, collectionPath), startButton.click()]);
  const stopButton = panel.getByRole('button', { name: '停止分析', exact: true });
  await stopButton.waitFor({ timeout: 120000 });
  const taskId = await pointer();
  assert.ok(taskId);
  const taskPath = `${collectionPath}/${taskId}`;
  assert.equal(await startButton.count(), 0);
  await Promise.all([waitForTaskResponse('POST', 200, taskPath + '/cancel'), stopButton.click()]);
  const stoppedAlert = page.getByRole('alert').filter({ hasText: '已停止' });
  await stoppedAlert.waitFor({ timeout: 30000 });
  const stoppedClass = await stoppedAlert.getAttribute('class');
  assert.ok(
    /ant-alert-info/.test(stoppedClass ?? ''),
    'cancelled should be info, got ' + stoppedClass,
  );
  assert.equal(await page.getByText('分析失败', { exact: true }).count(), 0);
  await assertNoStart();
  checks.push('cancelled shows an info status with no start action');

  // Seed rare terminal states in the disposable database, then restore over real HTTP.
  function seedTask(sql, ...values) {
    execFileSync(
      python,
      [
        '-c',
        'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute(sys.argv[2], sys.argv[3:]); c.commit(); c.close()',
        database,
        sql,
        ...values,
        taskId,
      ],
      { encoding: 'utf8', windowsHide: true },
    );
  }
  for (const [state, code, label] of [
    ['failed', 'AI_TASK_FAILED', '分析失败'],
    ['timed_out', 'AI_TASK_TIMEOUT', '分析超时'],
    ['revoked', 'AI_PURPOSE_REVOKED', '访问权限已撤销'],
  ]) {
    seedTask(
      'UPDATE ai_synthetic_tasks SET state=?, error_code=?, cancelled=0 WHERE task_id=?',
      state,
      code,
    );
    await Promise.all([waitForTaskResponse('GET', 200, taskPath), page.reload()]);
    await panel.getByRole('alert').filter({ hasText: label }).waitFor();
    await assertNoStart();
    checks.push(`${state} restores with no start action`);
  }
  seedTask('UPDATE ai_synthetic_tasks SET expires=0 WHERE task_id=?');
  await Promise.all([waitForTaskResponse('GET', 410, taskPath), page.reload()]);
  await notice.waitFor();
  assert.equal(await pointer(), null);
  await panel.locator('.ai-custom-question summary').click();
  await question.fill('有哪些值得关注的问题？');
  const [conflict] = await Promise.all([
    waitForTaskResponse('POST', 409, collectionPath),
    startButton.click(),
  ]);
  assert.equal((await conflict.json()).error.code, 'AI_CAMPAIGN_ALREADY_USED');
  await panel.getByText('这份测评的 AI 分析机会已使用。', { exact: true }).waitFor();
  await assertNoStart();
  checks.push('410 clears the pointer; an authoritative 409 hides further start attempts');

  console.log(JSON.stringify({ checks, pass: true }, null, 2));
} finally {
  if (browser) await browser.close().catch(() => {});
  frontend.kill('SIGKILL');
}
