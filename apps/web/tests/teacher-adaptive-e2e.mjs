// A synthetic HTTP upstream for the proposed V1.1 contract. No production parser is exercised.
import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import { existsSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rmdir, unlink, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const web = fileURLToPath(new URL('..', import.meta.url));
const artifacts = path.join(web, 'artifacts', 'teacher-adaptive-projects');
const python =
  process.env.EDU_TEST_PYTHON ??
  path.resolve(
    web,
    '../../.local/api-venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );
const temporary = await mkdtemp(path.join(tmpdir(), 'weiran-adaptive-e2e-'));
const workbook = path.join(temporary, '十三题合成测评.xlsx');
const credential = path.join(temporary, 'session.json');
const token = 'synthetic-adaptive-teacher-token';
const questions = Array.from({ length: 13 }, (_, index) => ({
  item_id: `q${index + 1}`,
  display_name: `第 ${index + 1} 题`,
  kind: 'question',
  scale: { minimum: 0, maximum: index === 12 ? 20 : 10, step: 1, unit: '分' },
}));
const participants = [
  ['A', ...questions.map((item, index) => item.scale.maximum - (index % 3))],
  ['B', ...questions.map((_, index) => index % 4)],
  ['C', ...questions.map((item, index) => item.scale.maximum / 2 + (index % 2))],
];
const itemCount = questions.length;
const studentCount = participants.length;
const date = '2026-11-03T01:00:00Z';
const calls = [];
const unexpected = [];
const summaries = [];
let preview, batch, browser, frontend, page;
let saved = false;
const digest = (buffer) => createHash('sha256').update(buffer).digest('hex');
const statistic = (value) => ({
  value,
  exact: null,
  display: value === null ? '—' : value.toFixed(2),
});
const pageInfo = (returned, limit = returned, offset = 0, has_more = false) => ({
  offset,
  limit,
  returned,
  has_more,
});
const summary = {
  detected_student_count: studentCount,
  complete_student_count: studentCount,
  parsed_evaluation_count: itemCount * studentCount,
  error_count: 0,
  warning_count: 0,
};
const quality = {
  report_version: 'synthetic-v1.1',
  parser_version: 'synthetic-v1.1',
  status: 'passed',
  validation_passed: true,
  requires_warning_acknowledgement: false,
  summary: {
    ...summary,
    valid_score_count: itemCount * studentCount,
    missing_score_count: 0,
    invalid_score_count: 0,
    unknown_dimension_row_count: 0,
    affected_student_count: 0,
    students_with_errors: 0,
    students_with_warnings: 0,
    workbook_issue_count: 0,
  },
  dimensions: [],
  issue_groups: [],
  issues: [],
};
function projectStatistics(item, inputScores) {
  const scores = [...inputScores].sort((a, b) => a - b);
  return {
    item_id: item.item_id,
    valid_count: scores.length,
    missing_count: studentCount - scores.length,
    invalid_count: 0,
    mean: statistic(
      scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null,
    ),
    median: statistic(scores[1] ?? null),
    minimum: statistic(scores[0] ?? null),
    maximum: statistic(scores.at(-1) ?? null),
    distribution: scores.map((score) => ({
      score,
      count: 1,
      denominator: scores.length,
      ratio: statistic(1 / scores.length),
    })),
  };
}
const item_statistics = questions.map((item, index) =>
  projectStatistics(
    item,
    participants.map((row) => row[index + 1]),
  ),
);
const competencies = ['迭代反思', '数据清洗', '证据取舍', '条件验证', '代码解释'].map(
  (display_name, index) => ({
    item_id: `custom-${index}`,
    display_name,
    kind: 'competency',
    scale: { minimum: 0, maximum: 10, step: 1, unit: '分' },
  }),
);
const threeCompetencies = [competencies[4], competencies[1], competencies[0]];
const cases = [
  { id: 'competency-five', title: '五项能力', items: competencies, radar: true, statistics: true },
  {
    id: 'competency-three',
    title: '三项能力',
    items: threeCompetencies,
    radar: true,
    statistics: true,
  },
  {
    id: 'competency-mixed',
    title: '混合量表',
    items: threeCompetencies.map((item, index) =>
      index === 1 ? { ...item, scale: { ...item.scale, maximum: 20 } } : item,
    ),
    radar: false,
    statistics: true,
  },
  {
    id: 'competency-unavailable',
    title: '未开放统计',
    items: threeCompetencies,
    radar: false,
    statistics: false,
  },
];
const fixtureBatch = (scenario) => ({
  ...batch,
  batch_id: scenario.id,
  snapshot_id: `snapshot-${scenario.id}`,
  title: scenario.title,
  evaluation_count: studentCount * scenario.items.length,
  recognized_items: scenario.items,
  available_analyses: scenario.statistics ? ['item_statistics'] : [],
});

const upstream = createServer(async (request, response) => {
  const send = (status, value) =>
    response
      .writeHead(status, {
        'Content-Type': 'application/json',
      })
      .end(JSON.stringify(value));
  const url = new URL(request.url, 'http://127.0.0.1');
  const route = url.pathname.replace(/^\/api\/v1\/edu/, '');
  calls.push(`${request.method} ${route}`);
  try {
    assert.equal(request.headers.authorization, `Bearer ${token}`);
    switch (`${request.method} ${route}`) {
      case 'GET /me':
        return send(200, { teacher_id: 'synthetic-teacher' });
      case 'GET /scopes':
        return send(200, {
          items: [{ data_scope_id: 'synthetic-scope', label: '合成验收', can_import: true }],
        });
      case 'GET /batches': {
        const items = saved ? [batch, ...cases.map(fixtureBatch)] : [];
        return send(200, {
          items,
          total_count: items.length,
          page: pageInfo(items.length),
        });
      }
      case 'POST /imports/preview': {
        const body = await new Response(request, {
          headers: { 'content-type': request.headers['content-type'] },
        }).formData();
        const file = body.get('file');
        assert.equal(file.name, path.basename(workbook));
        const fileSha = digest(Buffer.from(await file.arrayBuffer()));
        assert.equal(fileSha, digest(await readFile(workbook)));
        assert.equal(body.get('data_scope_id'), 'synthetic-scope');
        const title = String(body.get('title'));
        const rows = participants.flatMap((row, student) =>
          questions.map((item, index) => ({
            row_key: `${student}-${item.item_id}`,
            preview_student_id: `preview-${student}`,
            display_label: `参评者 ${student + 1}`,
            item_id: item.item_id,
            dimension_code: null,
            score: row[index + 1],
            score_raw: String(row[index + 1]),
            score_state: 'valid',
            comment: null,
            comment_status: 'empty',
            issue_codes: [],
            source: {
              file_sha256: fileSha,
              sheet_name: '逐题成绩',
              row_number: student + 2,
              dimension_cell: `${String.fromCharCode(66 + index)}1`,
              score_cell: `${String.fromCharCode(66 + index)}${student + 2}`,
              comment_cell: '',
            },
          })),
        );
        preview = {
          import: {
            import_id: 'synthetic-import',
            status: 'preview_ready',
            data_scope_id: 'synthetic-scope',
            title,
            course: null,
            assessment_date: null,
            revision_of_batch_id: null,
            file_sha256: fileSha,
            content_sha256: fileSha,
            content_hash_version: 'synthetic',
            parser_version: 'synthetic-v1.1',
            rubric_version: null,
            preview_version: 'preview-1',
            created_at: date,
            expires_at: date,
            committed_batch_id: null,
          },
          preview_available: true,
          recognized_items: questions.map((item) => ({ ...item, score_row_count: studentCount })),
          summary,
          student_samples: null,
          student_page: null,
          rows,
          row_page: pageInfo(rows.length),
          issues: [],
          issue_page: pageInfo(0),
        };
        batch = {
          batch_id: 'synthetic-batch',
          snapshot_id: 'synthetic-snapshot',
          data_scope_id: 'synthetic-scope',
          title,
          course: null,
          assessment_date: null,
          student_count: studentCount,
          evaluation_count: summary.parsed_evaluation_count,
          rubric_version: null,
          parser_version: 'synthetic-v1.1',
          file_sha256: fileSha,
          content_sha256: fileSha,
          content_hash_version: 'synthetic',
          revision_of_batch_id: null,
          is_current: true,
          created_at: date,
          recognized_items: questions,
          available_analyses: ['item_statistics'],
        };
        return send(200, preview);
      }
      case 'GET /imports/synthetic-import': {
        const offset = Number(url.searchParams.get('row_offset') ?? 0);
        const rows = preview.rows.slice(offset, offset + 10);
        return send(200, {
          ...preview,
          rows,
          row_page: pageInfo(rows.length, 10, offset, offset + rows.length < preview.rows.length),
        });
      }
      case 'GET /imports/synthetic-import/quality-report':
        return send(200, { ...quality, file_sha256: preview.import.file_sha256 });
      case 'POST /imports/synthetic-import/commit': {
        const body = await new Response(request).json();
        assert.equal(body.preview_version, 'preview-1');
        saved = true;
        return send(200, {
          import_id: 'synthetic-import',
          batch_id: batch.batch_id,
          snapshot_id: batch.snapshot_id,
          result: 'created',
        });
      }
      case 'GET /batches/synthetic-batch':
        assert.ok(saved);
        return send(200, batch);
      case 'GET /batches/synthetic-batch/statistics':
        assert.ok(saved);
        assert.deepEqual([...url.searchParams], [['snapshot_id', batch.snapshot_id]]);
        return send(200, {
          batch_id: batch.batch_id,
          snapshot_id: batch.snapshot_id,
          item_statistics,
        });
      default: {
        const match = /^\/batches\/([^/]+)(\/statistics)?$/.exec(route);
        const scenario = match && cases.find(({ id }) => id === match[1]);
        if (saved && request.method === 'GET' && scenario) {
          const target = fixtureBatch(scenario);
          if (!match[2]) return send(200, target);
          assert.ok(scenario.statistics, 'unadvertised analysis must not be requested');
          assert.deepEqual([...url.searchParams], [['snapshot_id', target.snapshot_id]]);
          return send(200, {
            batch_id: target.batch_id,
            snapshot_id: target.snapshot_id,
            item_statistics: scenario.items.map((item, index) =>
              projectStatistics(
                item,
                scenario.id === 'competency-mixed' && index === 2
                  ? []
                  : [0, item.scale.maximum / 2, item.scale.maximum],
              ),
            ),
          });
        }
        throw new Error(`Unexpected route ${request.method} ${route}`);
      }
    }
  } catch (error) {
    unexpected.push(error.message);
    send(500, {
      error: { code: 'SYNTHETIC_CONTRACT_ERROR', message: 'Unexpected fixture request' },
    });
  }
});

try {
  await mkdir(artifacts, { recursive: true });
  execFileSync(
    python,
    [
      '-c',
      [
        'import json, sys',
        'from openpyxl import Workbook',
        'book = Workbook()',
        'book.active.title = "逐题成绩"',
        'for row in json.loads(sys.argv[2]): book.active.append(row)',
        'book.save(sys.argv[1])',
      ].join('\n'),
      workbook,
      JSON.stringify([
        ['学生标识', ...questions.map((item) => item.display_name)],
        ...participants,
      ]),
    ],
    {
      windowsHide: true,
      env: { ...process.env, PYTHONUTF8: '1' },
    },
  );
  await writeFile(credential, JSON.stringify({ synthetic_only: true, access_token: token }));
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  const probe = createServer();
  probe.listen(0, '127.0.0.1');
  await once(probe, 'listening');
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  frontend = spawn(
    process.execPath,
    ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)],
    {
      cwd: web,
      windowsHide: true,
      stdio: 'inherit',
      env: {
        ...process.env,
        EDU_API_ORIGIN: `http://127.0.0.1:${upstream.address().port}`,
        EDU_TEST_MODE: '1',
        EDU_TEST_SESSION_FILE: credential,
        NEXT_TELEMETRY_DISABLED: '1',
      },
    },
  );
  const deadline = Date.now() + 30_000;
  while (true) {
    assert.equal(frontend.exitCode, null, 'Next exited before startup');
    assert.ok(Date.now() < deadline, 'Next startup timed out');
    const ready = await fetch(origin, { signal: AbortSignal.timeout(1000) }).catch(() => null);
    if (ready?.ok) break;
    await sleep(200);
  }
  const executablePath = [
    process.env.EDU_TEST_BROWSER,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
  ].find((value) => value && existsSync(value));
  browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
  });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', (error) => unexpected.push(error.message));
  await page.goto(`${origin}/insights`);
  await page.getByRole('heading', { name: '上传测评资料', exact: true }).waitFor();
  await page.locator('input[type=file]').setInputFiles(workbook);
  await page.getByText('文件检查通过').waitFor();
  await page.getByText('评分项目与量表', { exact: true }).click();
  await page.getByText('第 13 题 · 0–20 分', { exact: true }).waitFor();
  await page.screenshot({ path: path.join(artifacts, '13-upload-preview.png'), fullPage: true });
  await page.getByRole('button', { name: '下一步：确认导入' }).click();
  await page.getByRole('button', { name: '确认导入', exact: true }).click();
  const table = page.getByRole('table', { name: '逐题统计' });
  await table.waitFor();
  assert.equal(await table.getByRole('row').count(), 14);
  const last = table.getByRole('row', { name: /第 13 题/ });
  assert.match(await last.innerText(), /0–20 分.*3.*10\.00/s);
  assert.equal(await page.getByRole('img', { name: /均值/ }).count(), 0);
  await page.screenshot({ path: path.join(artifacts, '13-diagnosis.png'), fullPage: true });
  summaries.push('upload / preview / commit / 13 statistics rows');

  await page.getByRole('link', { name: '画像探索' }).click();
  await page.getByRole('combobox', { name: '选择题目' }).fill('第 13 题');
  // Ant Design renders a separate, hidden ARIA option when virtual scrolling is active.
  await page.getByText('第 13 题', { exact: true }).click();
  await page.getByRole('heading', { name: '第 13 题', exact: true }).waitFor();
  const distribution = page.getByRole('table', { name: '得分分布' });
  assert.equal(await distribution.getByRole('row').count(), 4);
  assert.match(await distribution.getByRole('row').last().innerText(), /20.*1.*33\.33%/s);
  await page.screenshot({ path: path.join(artifacts, '13-question-explorer.png'), fullPage: true });
  summaries.push('choose question 13 / original scale / real fixture distribution');

  await page.getByRole('link', { name: '分析报告' }).click();
  await page.getByText('当前测评暂不支持生成或导出分析报告。').waitFor();
  assert.equal(await page.getByRole('button', { name: /生成|导出|下载/ }).count(), 0);
  await page.screenshot({ path: path.join(artifacts, '13-reports.png'), fullPage: true });
  await page.reload();
  await page.getByText('当前测评暂不支持生成或导出分析报告。').waitFor();
  await page.getByRole('link', { name: '学情诊断' }).click();
  await table.waitFor();
  assert.equal(await table.getByRole('row').count(), 14);
  summaries.push('unavailable report has no action / reload retains the recognized items');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('link', { name: '画像探索' }).click();
  const question = page.getByRole('combobox', { name: '选择题目' });
  await question.fill('第 12 题');
  await question.press('Enter');
  await page.getByRole('heading', { name: '第 12 题', exact: true }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(artifacts, '13-mobile-keyboard.png'), fullPage: true });
  summaries.push('390px question explorer / keyboard search and selection');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('link', { name: '学情诊断' }).click();
  await table.waitFor();

  for (const scenario of cases) {
    await page.getByRole('combobox', { name: '当前正式测评批次' }).click();
    await page.getByText(scenario.title, { exact: true }).click();
    const projectTable = page.getByRole('table', { name: '评分项目统计' });
    await projectTable.waitFor();
    const names = scenario.items.map((item) => item.display_name);
    assert.deepEqual(
      await projectTable.locator('tbody tr[data-row-key] td:first-child').allTextContents(),
      names,
    );
    const radar = page.getByRole('img', { name: /均值/ });
    if (scenario.radar) {
      await radar.locator('svg').waitFor();
      assert.deepEqual(
        (await radar.getAttribute('aria-label')).split('，').map((label) => label.split('均值')[0]),
        names,
      );
    } else {
      assert.equal(await radar.count(), 0);
    }
    if (scenario.id === 'competency-mixed') {
      assert.match(
        await projectTable.getByRole('row', { name: /数据清洗/ }).innerText(),
        /0–20 分.*10\.00/s,
      );
      await projectTable.getByRole('row', { name: /迭代反思.*暂无有效评分/ }).waitFor();
    }
    if (!scenario.statistics) {
      await page.getByText('当前测评尚未开放项目统计。').waitFor();
      assert.equal(await projectTable.getByRole('columnheader', { name: '平均分' }).count(), 0);
      assert.ok(!calls.includes(`GET /batches/${scenario.id}/statistics`));
    }
    await page.screenshot({ path: path.join(artifacts, `${scenario.id}.png`), fullPage: true });
  }
  summaries.push(
    'five and three custom competency axes / mixed scales / no unadvertised statistics',
  );
  assert.deepEqual(unexpected, []);
  await writeFile(
    path.join(artifacts, 'result.json'),
    JSON.stringify(
      {
        synthetic_only: true,
        frontend_revision: process.env.EDU_TEST_FRONTEND_REVISION ?? null,
        status: 'complete',
        browser: browser.version(),
        questions: itemCount,
        participants: studentCount,
        summaries,
        upstream_calls: calls,
        unexpected,
      },
      null,
      2,
    ),
  );
  console.log(summaries.join('\n'));
} catch (error) {
  if (page) await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true });
  await writeFile(
    path.join(artifacts, 'failure.json'),
    JSON.stringify({ summaries, calls, unexpected }, null, 2),
  );
  throw error;
} finally {
  await browser?.close();
  if (frontend && frontend.exitCode === null) {
    const exited = once(frontend, 'exit');
    frontend.kill();
    await exited;
  }
  await new Promise((resolve) => upstream.close(resolve));
  await Promise.all([workbook, credential].map((file) => unlink(file).catch(() => {})));
  await rmdir(temporary);
}
