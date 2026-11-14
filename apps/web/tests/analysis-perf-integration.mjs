// Browser acceptance for the exact #90 + #91 combination. No product behavior is patched here.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createServer } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { chromium } from 'playwright';

const tests = fileURLToPath(new URL('.', import.meta.url));
const web = path.resolve(tests, '..');
const repo = path.resolve(web, '../..');
const artifacts = path.join(web, 'artifacts/analysis-perf-integration');
const aiOnly = process.env.EDU_INTEGRATION_AI_ONLY === '1';
await mkdir(artifacts, { recursive: true });
const python = process.env.EDU_TEST_PYTHON;
assert.ok(python, 'Use the locked Python environment');
const environment = {
  ...process.env,
  PYTHONPATH: `${repo};${path.join(repo, 'apps/api')}`,
  PYTHONUTF8: '1',
};
const report = {
  coverage: aiOnly ? 'ai-and-report-entrance-only' : 'full-analysis-ai-and-report-entrance',
  syntheticOnly: true,
  realQwen: 'not_run',
  checks: [],
  network: [],
  pageErrors: [],
  analysis: [],
};
let hostDirectory;
let browser,
  page,
  frontend,
  checkpoint = 'host-start';
const host = spawn(python, ['-u', path.join(tests, 'integration-synthetic-host.py')], {
  cwd: repo,
  env: environment,
  stdio: ['pipe', 'pipe', 'pipe'],
  windowsHide: true,
});
let hostOutput = '',
  hostErrors = '';
host.stderr.on('data', (chunk) => {
  hostErrors = (hostErrors + chunk.toString()).slice(-4000);
});
const connectionReady = new Promise((resolve, reject) => {
  const timer = setTimeout(
    () => reject(Error('Registered synthetic host readiness timeout')),
    300000,
  );
  host.stdout.on('data', (chunk) => {
    hostOutput += chunk.toString();
    for (const line of hostOutput.split('\n')) {
      try {
        const data = JSON.parse(line);
        if (data.ready) {
          clearTimeout(timer);
          resolve(data.connection);
        }
      } catch {
        /* uvicorn startup text is not a readiness record */
      }
    }
  });
  host.once('exit', (code) => {
    clearTimeout(timer);
    reject(Error(`Owned host exited ${code}: ${hostErrors}`));
  });
});
connectionReady.catch(() => {});
try {
  const connectionPath = await connectionReady;
  hostDirectory = path.dirname(connectionPath);
  const connection = JSON.parse(await readFile(connectionPath, 'utf8'));
  assert.equal(connection.synthetic_only, true);
  assert.equal(connection.execution_origin, 'http_mock');
  assert.equal(connection.real_qwen, 'not_run');
  const sessionFile = path.join(path.dirname(connectionPath), 'web-session.private.json');
  await writeFile(
    sessionFile,
    JSON.stringify({ synthetic_only: true, access_token: connection.bearer_token }),
  );
  const reservation = createServer();
  reservation.listen(0, '127.0.0.1');
  await once(reservation, 'listening');
  const port = reservation.address().port;
  await new Promise((resolve) => reservation.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  frontend = spawn(
    process.execPath,
    ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)],
    {
      cwd: web,
      env: {
        ...environment,
        EDU_TEST_MODE: '1',
        EDU_AI_SYNTHETIC_MODE: '1',
        EDU_TEST_SESSION_FILE: sessionFile,
        EDU_API_ORIGIN: connection.api_origin,
        NEXT_TELEMETRY_DISABLED: '1',
      },
      stdio: 'ignore',
      windowsHide: true,
    },
  );
  for (let attempt = 0; ; attempt++) {
    assert.equal(frontend.exitCode, null);
    assert.ok(attempt < 100, 'Next readiness timeout');
    if ((await fetch(origin, { signal: AbortSignal.timeout(1500) }).catch(() => null))?.ok) break;
    await sleep(200);
  }
  browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.EDU_TEST_BROWSER ||
      path.join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe'),
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(120000);
  page.on('pageerror', (error) => report.pageErrors.push(error.message));
  const started = new Map(),
    pending = new Set();
  page.on('request', (request) => {
    if (request.url().includes('/api/edu/')) {
      assert.equal(
        request.headers().authorization,
        undefined,
        'Synthetic relay credential stays server-side',
      );
      started.set(request, performance.now());
    }
    assert.ok(!/qwen|dashscope/i.test(request.url()), 'No real model requests');
    assert.ok(
      !request.url().includes('/review-submissions'),
      'This acceptance only checks the report entrance',
    );
  });
  page.on('response', (response) => {
    const request = response.request();
    if (!started.has(request)) return;
    const entry = {
      path: new URL(request.url()).pathname,
      method: request.method(),
      status: response.status(),
      ms: Math.round(performance.now() - started.get(request)),
    };
    report.network.push(entry);
    if (/\/discoveries\/[0-9a-f]{24}\/query$/.test(entry.path) && entry.status === 200) {
      const read = response
        .json()
        .then((data) => {
          entry.detail = data;
        })
        .catch(() => {})
        .finally(() => pending.delete(read));
      pending.add(read);
    }
  });
  const screenshot = async (name) =>
    page.screenshot({ path: path.join(artifacts, name + '.png'), fullPage: true });
  const focus = () => page.locator('.discoveries-panel [data-testid=discovery-card]');
  const me = await (await fetch(origin + '/api/edu/me')).json();
  async function openCase(name, requireFocus = true) {
    checkpoint = 'case-' + name;
    console.log(JSON.stringify({ checkpoint }));
    if (page.url() === 'about:blank') {
      await page.addInitScript(
        ({ teacher, batch }) => {
          const key = 'weiran.edu.current-batch.' + teacher;
          if (!localStorage.getItem(key)) localStorage.setItem(key, batch);
        },
        { teacher: me.teacher_id, batch: connection.batches[name].batch_id },
      );
      await page.goto(origin + '/insights');
    } else {
      await page.evaluate(
        ({ teacher, batch }) => localStorage.setItem('weiran.edu.current-batch.' + teacher, batch),
        { teacher: me.teacher_id, batch: connection.batches[name].batch_id },
      );
      await page.reload();
    }
    if (requireFocus) await focus().first().waitFor();
    else await page.getByLabel('想了解什么？', { exact: true }).waitFor();
    await page.waitForLoadState('networkidle');
    await Promise.all([...pending]);
  }
  async function readFocus(name, count) {
    const cards = focus();
    assert.ok(
      (await cards.count()) >= 2 && (await cards.count()) <= 3,
      'Preserve #90 representative cards',
    );
    const titles = await cards.locator('h3').allInnerTexts();
    assert.equal(new Set(titles).size, titles.length);
    assert.ok(
      titles.every((title) => !['查看两组学生的评分比较', '查看两项评分的关联'].includes(title)),
    );
    for (const summary of await cards.locator('.focus-explanation > summary').all())
      await summary.click();
    const explanations = await cards.locator('.focus-explanation p').allInnerTexts();
    const ids = await cards.evaluateAll((elements) =>
      elements.map((element) => element.dataset.discoveryId),
    );
    const details = ids.map(
      (id) => report.network.findLast((row) => row.detail?.discovery_id === id)?.detail,
    );
    assert.ok(details.every((detail) => detail?.scope.scope_record_count === count));
    assert.equal(explanations.length, details.length);
    details.forEach((detail, index) => {
      const text = explanations[index];
      assert.ok(text.length > 80 && /查看|核查|核对/.test(text));
      // A legitimate group description can state 24 + 48 rather than repeat 72.
      // Check the actual finding's denominators, not one literal class size.
      const metrics = detail.metrics;
      const counts = {
        dimension_relation: [metrics.paired_count],
        group_difference: [metrics.target_count, metrics.remainder_count],
        rare_combination: [metrics.pair_count, metrics.match_count],
        boundary_sensitivity: [metrics.strict_count, metrics.inclusive_count],
      }[detail.type];
      assert.ok(counts?.length && counts.every(Number.isSafeInteger));
      assert.ok(
        counts.every((number) => text.includes(String(number))),
        detail.discovery_id,
      );
    });
    report.analysis.push({ case: name, students: count, titles, explanations });
    await screenshot('diagnosis-' + name);
    return { titles, explanations };
  }
  if (!aiOnly) {
    await openCase('balanced72');
    const first = await readFocus('balanced72', 72);
    await page.getByRole('link', { name: '画像探索', exact: true }).click();
    await page.getByText('应用筛选', { exact: true }).waitFor();
    await page.getByRole('link', { name: '学情诊断', exact: true }).click();
    await focus().first().waitFor();
    assert.deepEqual(await focus().locator('h3').allInnerTexts(), first.titles);
    report.checks.push(
      '72-student real findings retain differentiated titles, complete explanations and return navigation',
    );
    await openCase('overlap96');
    const second = await readFocus('overlap96', 96);
    assert.notDeepEqual(first.explanations, second.explanations);
    report.checks.push(
      '96-student assessment produces different source-backed analysis without a fixed class size',
    );
    checkpoint = 'static-detail-chart-evidence';
    await focus().first().getByRole('button', { name: '查看发现与依据', exact: true }).click();
    await page.getByTestId('discovery-chart').locator('svg').waitFor();
    const drawer = page.getByRole('dialog', { name: '发现详情', exact: true });
    await drawer
      .getByRole('button', { name: /^(配对评分学生|符合条件的学生)$/ })
      .first()
      .click();
    await page.getByTestId('discovery-record-count').waitFor();
    const proofReady = page.waitForResponse(
      (r) => /\/evidence\/[^/?]+\?/.test(r.url()) && r.status() === 200,
    );
    await page
      .locator('.ant-drawer-open')
      .last()
      .getByRole('button', { name: /评分依据$/ })
      .first()
      .click();
    const proof = await (await proofReady).json();
    assert.equal(proof.snapshot_id, connection.batches.overlap96.snapshot_id);
    await page.getByText('原始数值：', { exact: false }).waitFor();
    await screenshot('static-score-evidence');
    for (let i = 0; i < 3; i++)
      await page.locator('.ant-drawer-open').last().locator('.ant-drawer-close').click();
    report.checks.push(
      'discovery detail, SVG chart, real participant records and single score evidence 200',
    );
  }
  await openCase('normal', false);
  if (aiOnly) {
    report.normalDiagnosisLoaded = await focus().first().isVisible();
    if (report.normalDiagnosisLoaded) {
      await readFocus('normal53', 53);
      report.checks.push(
        'normal 53-student diagnosis titles and complete numerical explanation visible',
      );
    }
  }
  checkpoint = 'unknown-question';
  const question = page.getByLabel('想了解什么？', { exact: true });
  const unsupported = page.waitForResponse(
    (r) => r.url().endsWith('/ai-investigation-tasks') && r.status() === 422,
  );
  await question.fill('帮我分析一下');
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  assert.equal((await (await unsupported).json()).error.code, 'AI_GOAL_UNSUPPORTED');
  assert.equal(await question.inputValue(), '帮我分析一下');
  await page.getByText(/目前可以核查评分差别/).waitFor();
  await screenshot('unsupported-question-guidance');
  checkpoint = 'quick-question-ai';
  const createsBefore = report.network.filter(
    (row) => row.method === 'POST' && row.path.endsWith('/ai-investigation-tasks'),
  ).length;
  await page
    .getByRole('group', { name: '快捷提问' })
    .getByRole('button', { name: '先看看哪些评分值得核查', exact: true })
    .click();
  assert.equal(await question.inputValue(), '有哪些值得关注的问题？');
  assert.equal(
    report.network.filter(
      (row) => row.method === 'POST' && row.path.endsWith('/ai-investigation-tasks'),
    ).length,
    createsBefore,
  );
  const created = page.waitForResponse(
    (r) => r.url().endsWith('/ai-investigation-tasks') && r.status() === 202,
  );
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  const task = await (await created).json();
  await page.getByTestId('ai-fact-card').first().waitFor();
  const taskPath = `/api/edu/batches/${connection.batches.normal.batch_id}/ai-investigation-tasks/${task.task_id}?snapshot_id=${connection.batches.normal.snapshot_id}`;
  const terminal = await page.evaluate(async (url) => {
    const response = await fetch(url);
    return { status: response.status, data: await response.json() };
  }, taskPath);
  assert.equal(terminal.status, 200);
  assert.equal(terminal.data.state, 'completed');
  assert.equal(terminal.data.result.real_qwen, 'not_run');
  await page
    .getByText(/受控模拟分析；未调用真实 Qwen/)
    .first()
    .waitFor();
  report.ai = {
    createStatus: 202,
    statusStatus: terminal.status,
    state: terminal.data.state,
    stopReason: terminal.data.result.stop_reason,
    realQwen: terminal.data.result.real_qwen,
    factCount: terminal.data.result.facts.length,
    profileCount: terminal.data.result.profile_projection?.profiles.length ?? 0,
  };
  assert.equal(await page.getByTestId('ai-profile-card').count(), report.ai.profileCount);
  await screenshot('ai-completed');
  await page
    .getByTestId('ai-fact-card')
    .first()
    .getByRole('button', { name: '查看评分依据', exact: true })
    .click();
  const sources = page.getByRole('dialog', { name: '评分依据', exact: true });
  await sources.getByRole('button', { name: '查看原评分依据', exact: true }).first().click();
  await page.getByText('原始数值：', { exact: false }).waitFor();
  await screenshot('ai-score-evidence');
  await page.locator('.ant-drawer-open').last().locator('.ant-drawer-close').click();
  await sources.locator('.ant-drawer-close').click();
  checkpoint = 'ai-profile-evidence';
  assert.ok(report.ai.profileCount > 0, 'Exercise an actual AI profile, not an empty list');
  const profileScoresReady = page.waitForResponse(
    (r) => r.url().endsWith('/profiles/scores/query') && r.status() === 200,
  );
  await page
    .getByTestId('ai-profile-card')
    .first()
    .getByRole('button', {
      name: /^查看.+评分依据/,
    })
    .first()
    .click();
  const profileScores = await (await profileScoresReady).json();
  assert.equal(profileScores.detail_status, 'available');
  assert.ok(profileScores.items.length > 0);
  const profileDrawer = page.getByRole('dialog', { name: / · 评分依据$/ });
  await profileDrawer.getByRole('button', { name: '查看原评分依据', exact: true }).first().click();
  await page.getByText('原始数值：', { exact: false }).waitFor();
  await screenshot('ai-profile-score-evidence');
  await page.locator('.ant-drawer-open').last().locator('.ant-drawer-close').click();
  await profileDrawer.locator('.ant-drawer-close').click();
  report.checks.push('AI profile score query 200 and its original score evidence visible');
  report.checks.push(
    'unknown question 422 preserved; shortcut only fills; explicit creation 202, polling completed 200, real AI Fact evidence visible; no real model',
  );
  await page.setViewportSize({ width: 390, height: 844 });
  report.mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > innerWidth,
  );
  await screenshot('combined-mobile');
  await page.setViewportSize({ width: 1440, height: 1000 });
  checkpoint = 'report-entrance';
  await page.getByRole('link', { name: '分析报告', exact: true }).click();
  await page.getByRole('heading', { name: '分析报告', level: 1, exact: true }).waitFor();
  await page.waitForLoadState('networkidle');
  await screenshot('report-entrance');
  report.checks.push(
    'report entrance renders without submitting review-submissions or writing a formal report',
  );
  assert.deepEqual(report.pageErrors, []);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = { checkpoint, message: error.stack || String(error) };
  if (page)
    await page
      .screenshot({
        path: path.join(artifacts, aiOnly ? 'ai-failure.png' : 'failure.png'),
        fullPage: true,
      })
      .catch(() => {});
  process.exitCode = 1;
} finally {
  // Detail bodies in this file are synthetic aggregates, never raw student identities or credentials.
  await writeFile(
    path.join(artifacts, aiOnly ? 'browser-ai-results.json' : 'browser-results.json'),
    JSON.stringify(report, null, 2) + '\n',
  );
  await browser?.close();
  if (frontend?.exitCode === null) frontend.kill();
  const hostStopped = once(host, 'exit').catch(() => {});
  if (host.exitCode === null && hostDirectory)
    await writeFile(path.join(hostDirectory, 'stop.flag'), 'stop\n');
  await Promise.race([hostStopped, sleep(45000)]);
  if (host.exitCode === null) host.kill();
  console.log(
    JSON.stringify({
      passed: report.passed,
      failure: report.failure,
      checks: report.checks,
      mobileOverflow: report.mobileOverflow,
    }),
  );
}
