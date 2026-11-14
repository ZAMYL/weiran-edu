// The normal success path talks exclusively to real FastAPI, SQLite and Next.js.
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { verifyReportArtifact } from './report-artifact-utils.mjs';

async function reportAction(region, name) {
  const button = region.getByRole('button', { name, exact: true });
  if (!(await button.isVisible()))
    await region.locator('.report-editor-info > details > summary').click();
  await button.click();
}

export async function teacherProductFlow(h) {
  const {
    page,
    origin,
    python,
    apiRoot,
    env,
    artifacts,
    admin,
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
  } = h;
  const checks = [];
  const wait = (test) => {
    const promise = page.waitForResponse(async (response) => {
      if (!(await test(response))) return false;
      // Ignore canceled same-URL background requests and keep the complete real
      // JSON before navigation can discard the response body.
      if ((await response.finished()) !== null) return false;
      const data = await response.json();
      response.json = async () => data;
      return true;
    });
    promise.catch(() => {});
    return promise;
  };
  const review = () => page.getByRole('region', { name: '分析核验', exact: true });
  const exportDialog = () => page.getByRole('dialog', { name: '导出测评报告', exact: true });
  const patch = async (route, body) =>
    page.evaluate(
      async ({ route, body }) => {
        const r = await fetch('/api/edu' + route, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        });
        return { status: r.status, body: await r.json() };
      },
      { route, body },
    );
  async function save(title) {
    const pending = wait(
      (r) => r.url().endsWith('/analysis-results') && r.request().method() === 'POST',
    );
    if (!new URL(page.url()).pathname.startsWith('/reports'))
      await page.getByRole('button', { name: '准备分析报告', exact: true }).click();
    await page.getByRole('button', { name: '保存本次分析', exact: true }).click();
    await page.getByLabel('分析名称', { exact: true }).fill(title);
    await page.getByRole('button', { name: '保存分析', exact: true }).click();
    const response = await pending;
    assert.equal(response.status(), 201, JSON.stringify(await response.json()));
    const data = await response.json();
    await review().locator('.saved-version').waitFor();
    return data;
  }
  async function download(format) {
    const pending = wait((r) => r.url().endsWith('/export-check'));
    await review().getByRole('button', { name: '导出测评报告', exact: true }).click();
    let preflight = await (await pending).json();
    if (format === 'md') {
      const next = wait(
        (r) => r.url().endsWith('/export-check') && r.request().postDataJSON().format === 'md',
      );
      await exportDialog().getByText('Markdown', { exact: true }).click();
      preflight = await (await next).json();
    }
    const ack = exportDialog().getByRole('checkbox');
    if (await ack.count()) await ack.check();
    const pendingFile = page.waitForEvent('download');
    await exportDialog()
      .getByRole('button', { name: format === 'md' ? '下载 Markdown' : '下载 XLSX', exact: true })
      .click();
    const file = await pendingFile;
    const bytes = await readFile(await file.path());
    const destination = path.join(
      artifacts,
      `${preflight.confirmed_finding_count ? 'confirmed' : 'empty'}-report.${format === 'md' ? 'md' : 'xlsx'}`,
    );
    await writeFile(destination, bytes);
    await exportDialog().locator('.ant-modal-close').click();
    return { bytes, preflight, destination };
  }
  await page.goto(origin);
  await page.locator('.live-workspace').waitFor();
  console.log('Joint E2E: import 53 synthetic students');
  const imported = await importThroughPage('contrast', '合成联合验收53人');
  assert.equal(imported.data.scope.scope_record_count, 53);
  const batch = { batch_id: imported.batchId, snapshot_id: imported.data.scope.snapshot_id };
  const spec = {
    snapshot_id: batch.snapshot_id,
    profile: 'edu.discovery.profile.v1',
    scope_conditions: [],
  };
  const base = `/batches/${batch.batch_id}`;
  const all = await post(`${base}/discoveries/query`, { ...spec, limit: 50 });
  assert.equal(all.status, 200);
  const index = all.body.items.findIndex((c) => c.type === 'group_difference');
  assert.ok(index >= 0);
  if ((await panel().getAttribute('open')) === null)
    await panel().locator(':scope > summary').click();
  const pageNumber = Math.floor(index / 10) + 1;
  if (pageNumber > 1) {
    const p = wait((r) => r.url().endsWith('/discoveries/query') && r.status() === 200);
    await panel().getByTitle(String(pageNumber), { exact: true }).click();
    await p;
  }
  if ((await panel().getAttribute('open')) === null)
    await panel().locator(':scope > summary').click();
  const chosen = all.body.items[index];
  await page.getByRole('combobox', { name: '查看发现类型', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText('全部发现', { exact: true }).click();
  for (const summary of await panel().locator('.discovery-type-group > summary').all())
    await summary.click();
  const chosenCard = panel().locator(`[data-discovery-id="${chosen.discovery_id}"]`);
  assert.equal(await chosenCard.getByRole('checkbox').count(), 0);
  const detailResponse = wait(
    (r) => r.url().endsWith(`/${chosen.discovery_id}/query`) && r.status() === 200,
  );
  await chosenCard.getByRole('button', { name: '查看具体评分', exact: true }).click();
  const detail = await (await detailResponse).json();
  assert.deepEqual(
    [detail.metrics.target_count, detail.metrics.remainder_count].sort((a, b) => a - b),
    [26, 27],
  );
  const outcome = detail.chart.find(
    (row) => row.dimension_code === detail.metrics.outcome_dimension,
  );
  assert.ok(outcome && !outcome.condition_defined && outcome.difference);
  assert.equal(Math.abs(outcome.difference.numerator / outcome.difference.denominator), 4);
  await page.getByTestId('discovery-chart').locator('svg').waitFor();
  await shot('01-discovery-desktop');
  const membersResponse = wait((r) => r.url().endsWith('/records/query') && r.status() === 200);
  await page
    .getByRole('dialog', { name: '发现详情', exact: true })
    .getByRole('button', { name: '看看这组学生', exact: true })
    .click();
  const original = await (await membersResponse).json();
  assert.equal(original.total_count, detail.metrics.target_count);
  await page
    .locator('.match-summary')
    .filter({ hasText: `${original.total_count} / 53` })
    .waitFor();
  assert.ok(page.url().includes('discovery='));
  const originalId = original.items[0].student_id;
  await page.reload();
  await page.locator('.live-workspace').waitFor();
  await page
    .locator('.match-summary')
    .filter({ hasText: `${original.total_count} / 53` })
    .waitFor();
  assert.ok((await page.locator('body').innerText()).includes('正在查看原发现群体'));
  const evidence = wait((r) => /\/evidence\/[^/?]+\?/.test(r.url()) && r.status() === 200);
  await page
    .getByRole('button', { name: /分，查看评分依据$/ })
    .first()
    .click();
  const proof = await (await evidence).json();
  assert.equal(proof.student_id, originalId);
  assert.equal(proof.batch_id, batch.batch_id);
  assert.equal(proof.snapshot_id, batch.snapshot_id);
  assert.equal(proof.comment_status, 'withheld');
  assert.equal(proof.comment, null);
  await shot('02-original-group-evidence');
  await closeTop();
  checks.push(
    '53: import/preview/commit → real discovery/chart → exact original 26/27 group → reload → owned evidence',
  );
  console.log('Joint E2E: save original discovery and review');
  await page.getByRole('button', { name: '准备分析报告', exact: true }).click();
  const initialDraft = page.locator('.report-draft-workspace');
  await initialDraft.locator('.report-draft-list, .live-error').first().waitFor();
  assert.equal(
    await initialDraft.locator('.live-error').count(),
    0,
    await initialDraft.innerText(),
  );
  const draftItems = page.locator('.report-draft-list input[type=checkbox]');
  await draftItems.first().waitFor();
  assert.ok((await draftItems.count()) >= 1);
  for (const checkbox of await draftItems.all()) {
    assert.equal(await checkbox.isChecked(), true);
    if ((await checkbox.getAttribute('value')) !== chosen.discovery_id) await checkbox.uncheck();
  }
  const created = await save('合成正式发现闭环');
  assert.deepEqual(created.analysis.query.conditions, []);
  assert.equal(created.analysis.matched_count, 53);
  assert.equal(created.analysis.members.length, 53);
  assert.equal(created.discovery_findings.length, 1);
  assert.equal(created.discovery_findings[0].discovery.discovery_id, chosen.discovery_id);
  assert.equal(created.discovery_findings[0].teacher_review.status, 'pending');
  assert.deepEqual(created.analysis.discovery.spec, spec);
  assert.equal(await page.getByRole('heading', { name: '本次报告内容', exact: true }).count(), 0);
  await review()
    .locator('.review-finding')
    .getByRole('radio', { name: '已确认', exact: true })
    .locator('xpath=ancestor::label[1]')
    .click();
  const updated = wait(
    (r) => r.url().endsWith('/' + created.result_id) && r.request().method() === 'PATCH',
  );
  await review().getByRole('button', { name: '保存核验结果', exact: true }).click();
  const reviewed = await (await updated).json();
  assert.equal(reviewed.version, 2);
  assert.equal(reviewed.discovery_findings[0].teacher_review.status, 'confirmed');
  await review().locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
  await navigation('学情诊断');
  await navigation('分析报告');
  await review().locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
  assert.equal(
    await review().getByRole('radio', { name: '已确认', exact: true }).isChecked(),
    true,
  );
  await navigation('学情诊断');
  await page.getByRole('button', { name: '准备分析报告', exact: true }).click();
  await page.getByRole('heading', { name: '本次报告内容', exact: true }).waitFor();
  await initialDraft.locator('.report-draft-list, .live-error').first().waitFor();
  assert.equal(
    await initialDraft.locator('.live-error').count(),
    0,
    await initialDraft.textContent(),
  );
  await draftItems.first().uncheck();
  assert.equal(await draftItems.first().isChecked(), false);
  await draftItems.first().check();
  await page.getByRole('button', { name: '已保存分析', exact: true }).click();
  await page.getByRole('button', { name: '查看与核验', exact: true }).first().click();
  await review().locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
  await shot('03-reviewed-discovery');
  const md = await download('md');
  assert.equal(md.preflight.confirmed_finding_count, 1);
  assert.equal(md.preflight.member_count, 53);
  const text = md.bytes.toString('utf8');
  assert.ok(text.includes(batch.batch_id) && text.includes(batch.snapshot_id));
  assert.ok(text.includes(chosen.discovery_id));
  assert.doesNotMatch(text, /合成私密姓名|未审核合成私密评语|合成私密依据/);
  const reportReference = {
    batchId: batch.batch_id,
    snapshotId: batch.snapshot_id,
    resultId: created.result_id,
    version: reviewed.version,
    studentCount: 53,
    discoveryIds: [chosen.discovery_id],
  };
  verifyReportArtifact({ ...md, format: 'md', python, env, reference: reportReference });
  assert.throws(
    () =>
      verifyReportArtifact({
        ...md,
        format: 'md',
        python,
        env,
        reference: { ...reportReference, version: reviewed.version + 1 },
      }),
    /mismatched 教师核验版本/,
  );
  const xlsx = await download('xlsx');
  assert.equal(xlsx.preflight.confirmed_finding_count, 1);
  const reopened = verifyReportArtifact({
    ...xlsx,
    format: 'xlsx',
    python,
    env,
    reference: reportReference,
  });
  assert.throws(
    () =>
      verifyReportArtifact({
        ...xlsx,
        format: 'xlsx',
        python,
        env,
        reference: { ...reportReference, snapshotId: 'synthetic-wrong-snapshot' },
      }),
    /mismatched 数据快照引用/,
  );
  await shot('04-report-after-review');
  const savedRoute = `${base}/analysis-results/${created.result_id}`;
  assert.equal(
    (
      await patch(savedRoute, {
        snapshot_id: batch.snapshot_id,
        expected_version: 2,
        note: '合成并发版本',
      })
    ).status,
    200,
  );
  await review().getByLabel('分析备注', { exact: true }).fill('合成本地变更');
  const conflict = wait(
    (r) => r.url().endsWith('/' + created.result_id) && r.request().method() === 'PATCH',
  );
  await review().getByRole('button', { name: '保存核验结果', exact: true }).click();
  assert.equal((await conflict).status(), 409);
  await review()
    .locator('details')
    .filter({ hasText: '错误码：ANALYSIS_RESULT_VERSION_MISMATCH' })
    .locator('summary')
    .click();
  await review().getByText('错误码：ANALYSIS_RESULT_VERSION_MISMATCH', { exact: true }).waitFor();
  await reportAction(review(), '加载最新版本');
  await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await review().locator('.saved-version').filter({ hasText: '已保存版本 3' }).waitFor();
  await reportAction(review(), '返回草稿');
  checks.push(
    'save stores full outer scope and selected formal discovery; confirmed persists; MD/XLSX reopened; actual 409 preserves edits',
  );
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  const history = page.locator('.batches-panel');
  const archivedRow = history.getByRole('row').filter({ hasText: '合成联合验收53人' });
  await archivedRow.getByRole('button', { name: '删除', exact: true }).click();
  const archive = wait((r) => r.url().endsWith('/archive') && r.request().method() === 'PATCH');
  await page.getByRole('dialog').getByRole('button', { name: '归档', exact: true }).click();
  assert.equal((await archive).status(), 200);
  await archivedRow.waitFor({ state: 'detached' });
  await page.reload();
  await page.locator('.live-workspace').waitFor();
  const openHistory = page.getByRole('button', { name: '查看历史测评', exact: true });
  if (!(await history.isVisible())) await openHistory.click();
  await history.getByText('已归档', { exact: true }).click();
  await archivedRow.getByRole('button', { name: '恢复', exact: true }).click();
  const restore = wait((r) => r.url().endsWith('/archive') && r.request().method() === 'PATCH');
  await page.getByRole('dialog').getByRole('button', { name: '恢复', exact: true }).click();
  assert.equal((await restore).status(), 200);
  await history.getByText('使用中', { exact: true }).click();
  await archivedRow.getByRole('button', { name: '查看学情', exact: true }).click();
  await page.getByRole('button', { name: '准备分析报告', exact: true }).waitFor();
  checks.push('account archive persists after reload and restores the original assessment');
  console.log('Joint E2E: empty discoveries, manual exploration and narrow screen');
  await navigation('学情诊断');
  const constant = await importThroughPage('constant', '合成同分17人');
  assert.equal(constant.data.scope.scope_record_count, 17);
  assert.deepEqual(constant.data.items, []);
  await page.getByText('当前范围没有符合条件的发现', { exact: true }).waitFor();
  const empty = await save('合成零发现报告');
  assert.equal(empty.analysis.matched_count, 17);
  assert.equal(empty.discovery_findings?.length ?? 0, 0);
  const emptyMd = await download('md');
  assert.equal(emptyMd.preflight.confirmed_finding_count, 0);
  await reportAction(review(), '返回草稿');
  await navigation('画像探索');
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await page.locator('.match-summary').filter({ hasText: '17 / 17' }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth);
  await shot('05-empty-discovery-manual-mobile');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await navigation('学情诊断');
  await selectBatch('合成联合验收53人');
  assert.ok(!page.url().includes('discovery='));
  await setToken(readerToken);
  admin('batch-grant-read', '--teacher-id', reader, '--batch-id', batch.batch_id);
  await page.reload();
  await page.locator('.live-workspace').waitFor();
  await page.getByRole('button', { name: '已保存分析', exact: true }).waitFor();
  assert.equal(
    await page.getByRole('button', { name: '准备分析报告', exact: true }).isDisabled(),
    true,
  );
  await page.getByRole('button', { name: '已保存分析', exact: true }).click();
  await page.getByRole('button', { name: '查看与核验', exact: true }).first().click();
  await review()
    .getByText('当前为只读权限，可查看核验结果和导出测评报告。', { exact: true })
    .waitFor();
  assert.equal(
    await review().getByRole('button', { name: '保存核验结果', exact: true }).count(),
    0,
  );
  const forbiddenSave = await post(`${base}/analysis-results`, {
    snapshot_id: batch.snapshot_id,
    conditions: [],
    include_dimensions: false,
    candidate_ids: [],
    title: '合成只读禁止保存',
  });
  assert.equal(forbiddenSave.status, 403);
  assert.equal(forbiddenSave.body.error.code, 'FORBIDDEN');
  await reportAction(review(), '返回草稿');
  await navigation('学情诊断');
  admin('batch-revoke-read', '--teacher-id', reader, '--batch-id', batch.batch_id);
  const denied = wait((r) => r.url().endsWith('/discoveries/query') && r.status() === 404);
  await page.getByRole('button', { name: '刷新发现', exact: true }).click();
  await denied;
  assert.equal(await page.getByTestId('discovery-card').count(), 0);
  await setToken(token);
  await page.reload();
  await page.locator('.live-workspace').waitFor();
  checks.push(
    '17 constant: zero qualified discoveries, valid empty report and manual exploration; batch switch clears origin; reader/revoked access enforced',
  );
  // Expire only this harness's synthetic credential in its disposable database.
  await page.getByRole('button', { name: '刷新发现', exact: true }).waitFor();
  await page.waitForLoadState('networkidle');
  execFileSync(
    python,
    [
      '-c',
      "import os,sqlite3,sys,tempfile; from pathlib import Path; p=Path(os.environ['WEIRAN_EDU_DATABASE']).resolve(); assert p.is_relative_to(Path(tempfile.gettempdir()).resolve()) and p.parent.name.startswith('weiran-discovery-flow-'); c=sqlite3.connect(p); c.execute(\"UPDATE teacher_tokens SET expires_at='2000-01-01T00:00:00+00:00' WHERE teacher_id=?\",(sys.argv[1],)); c.commit(); c.close()",
      h.teacher,
    ],
    { cwd: apiRoot, env, windowsHide: true },
  );
  await navigation('学情诊断');
  const expired = wait((r) => r.url().endsWith('/discoveries/query') && r.status() === 401);
  await page.getByRole('button', { name: '刷新发现', exact: true }).click();
  await expired;
  await page.getByRole('heading', { name: '正在连接数据', exact: true }).waitFor();
  assert.equal(await page.locator('.live-workspace').count(), 0);
  checks.push(
    'actually expired temporary credential receives 401 and clears all formal workspace data',
  );
  await writeFile(
    path.join(artifacts, 'product-result.json'),
    JSON.stringify(
      {
        frontendBaseline: execFileSync('git', ['rev-parse', 'HEAD'], {
          encoding: 'utf8',
          windowsHide: true,
        }).trim(),
        frontendRevision: process.env.EDU_TEST_FRONTEND_REVISION || null,
        backendRevision: process.env.EDU_TEST_BACKEND_REVISION || null,
        backendUploadRevision: process.env.EDU_TEST_UPLOAD_REVISION || null,
        backendDependency: 61,
        checks,
        chosenDiscovery: detail,
        originalGroupCount: original.total_count,
        savedResultId: created.result_id,
        reviewVersion: reviewed.version,
        reopenedSheets: reopened.sheets,
        note: 'Synthetic-only real Chromium. No mocked success responses or remote model calls.',
      },
      null,
      2,
    ),
  );
  return checks;
}

// Small real HTTP reproduction: an auth rejection must survive JSON forwarding.
export async function credentialRelayFlow({ page, origin, api, admin, setToken, token }) {
  console.log('Auth relay reproduction: setup');
  const teacher = admin('teacher-create', '--subject', 'synthetic-auth-relay').teacher_id;
  const credential = admin('token-issue', '--teacher-id', teacher, '--hours', '1').access_token;
  const route = '/batches/11111111-1111-4111-8111-111111111111/discoveries/query';
  const body = {
    snapshot_id: 'synthetic',
    scope_conditions: [],
    profile: 'edu.discovery.profile.v1',
  };
  await setToken(credential);
  await page.goto(origin);
  await page.locator('.live-workspace').waitFor();
  admin('token-revoke', '--teacher-id', teacher);
  console.log('Auth relay reproduction: direct POST');
  const direct = await fetch(api + '/api/v1/edu' + route, {
    signal: AbortSignal.timeout(10000),
    method: 'POST',
    headers: { authorization: `Bearer ${credential}`, 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  console.log(JSON.stringify({ directStatus: direct.status }));
  await direct.arrayBuffer();
  assert.equal(direct.status, 401);
  const relayed = await page.evaluate(
    async ({ route, body }) => {
      const r = await fetch('/api/edu' + route, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      return { status: r.status, body: await r.json() };
    },
    { route, body },
  );
  console.log(
    JSON.stringify({
      authRelay: { direct: direct.status, relayed: relayed.status, code: relayed.body.error?.code },
    }),
  );
  assert.equal(relayed.status, 401);
  assert.ok(relayed.body.error.request_id);
  await setToken(token);
  return [
    'real revoked credential JSON POST preserves upstream 401 and request ID through Next proxy',
  ];
}
