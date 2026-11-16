import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { verifyReportArtifact } from './report-artifact-utils.mjs';

export async function teacherIntegerFlow(h) {
  const { page, origin, artifacts, navigation, importThroughPage, post, closeTop, python, env } = h;
  const control = process.env.EDU_INTEGER_CONTROL === '1';
  assert.ok(
    !control || process.env.EDU_EXPECT_INTEGER_REJECTION !== '1',
    'Control cannot stand in for the precision rejection case',
  );
  const fixture = control ? 'contrast105' : 'precision105';
  const wait = (predicate) =>
    page.waitForResponse(async (response) => {
      if (!predicate(response) || (await response.finished())) return false;
      const body = await response.json();
      response.json = async () => body;
      return true;
    });
  await page.goto(`${origin}/insights`);
  await page.locator('.live-workspace').waitFor();
  const imported = await importThroughPage(
    fixture,
    control ? '合成105人安全整数对照' : '合成105人525评分精度验收',
  );
  assert.equal(imported.data.scope.batch_record_count, 105);
  assert.equal(imported.data.scope.scope_record_count, 105);
  const batch = await page.evaluate(async (batchId) => {
    const response = await fetch(`/api/edu/batches/${batchId}`);
    return response.json();
  }, imported.batchId);
  assert.equal(batch.student_count, 105);
  assert.equal(batch.evaluation_count, 525);
  const spec = {
    snapshot_id: batch.snapshot_id,
    profile: 'edu.discovery.profile.v1',
    scope_conditions: [],
  };
  const chosen = imported.data.items[0];
  assert.ok(chosen);
  const detailResult = await post(
    `/batches/${batch.batch_id}/discoveries/${chosen.discovery_id}/query`,
    spec,
  );
  assert.equal(detailResult.status, 200);
  if (!control && process.env.EDU_EXPECT_INTEGER_REJECTION !== '1') {
    assert.equal(typeof detailResult.body?.ranking?.score?.numerator, 'string');
    assert.equal(typeof detailResult.body.ranking.score.denominator, 'string');
    assert.ok(BigInt(detailResult.body.ranking.score.numerator) > BigInt(Number.MAX_SAFE_INTEGER));
    assert.ok(
      BigInt(detailResult.body.ranking.score.denominator) > BigInt(Number.MAX_SAFE_INTEGER),
    );
  }
  const stale = await post(`/batches/${batch.batch_id}/discoveries/query`, {
    ...spec,
    snapshot_id: 'stale-synthetic-snapshot',
  });
  assert.equal(stale.status, 409, 'An actual stale snapshot must still be rejected');
  const record = {
    synthetic_only: true,
    frontend_revision: process.env.EDU_TEST_FRONTEND_REVISION || null,
    backend_revision: process.env.EDU_TEST_BACKEND_REVISION || null,
    backend_upload_revision: process.env.EDU_TEST_UPLOAD_REVISION || null,
    students: 105,
    score_rows: 525,
    fixture,
    control_only: control,
    discoveries_http_status: 200,
    detail_http_status: 200,
    stale_snapshot_status: stale.status,
    wire_contract: 'EXACT_JSON_WIRE_V1',
    ranking: detailResult.body.ranking,
    browser: await page.evaluate(() => navigator.userAgent),
  };
  const focus = page.locator('.discoveries-panel');
  if (process.env.EDU_EXPECT_INTEGER_REJECTION === '1') {
    await focus.locator('.live-error').waitFor();
    assert.match(await focus.locator('.live-error').textContent(), /INVALID_API_RESPONSE/);
    assert.equal(await focus.getByTestId('discovery-card').count(), 0);
    await page.screenshot({ path: path.join(artifacts, '105-before-fix.png'), fullPage: true });
    await writeFile(
      path.join(artifacts, 'acceptance-record.json'),
      JSON.stringify(
        {
          ...record,
          status: 'bug_reproduced',
          full_workflow: 'not_run_blocked_by_decoder',
          error_code: 'INVALID_API_RESPONSE',
          product_accepted: false,
        },
        null,
        2,
      ),
    );
    return [
      'BUG REPRODUCTION ONLY: real 105/525 upload + diagnostic list/detail HTTP 200, UI INVALID_API_RESPONSE; full workflow blocked, not accepted',
    ];
  }
  const focusCard = focus.locator(`[data-discovery-id="${chosen.discovery_id}"]`);
  await focusCard.waitFor();
  assert.equal(await focus.locator('.live-error').count(), 0);
  await focusCard.getByRole('button', { name: '查看具体评分', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '发现详情', exact: true });
  await page.getByTestId('discovery-chart').locator('svg').waitFor();
  await page.screenshot({ path: path.join(artifacts, '105-discovery-detail.png'), fullPage: true });
  await drawer.getByRole('button', { name: '看看这组学生', exact: true }).click();
  await page.waitForURL('**/explorer?discovery=*');
  const pointer = JSON.parse(new URL(page.url()).searchParams.get('discovery'));
  assert.deepEqual(pointer.spec, spec);
  assert.equal(pointer.discovery_id, chosen.discovery_id);
  await page.locator('.match-summary').filter({ hasText: '/ 105' }).waitFor();
  const proofResponse = wait((r) => /\/evidence\/[^/?]+\?/.test(r.url()) && r.status() === 200);
  await page
    .getByRole('button', { name: /分，查看评分依据$/ })
    .first()
    .click();
  const proof = await (await proofResponse).json();
  assert.equal(proof.batch_id, batch.batch_id);
  assert.equal(proof.snapshot_id, batch.snapshot_id);
  assert.equal(proof.comment, null);
  await page.screenshot({ path: path.join(artifacts, '105-score-evidence.png'), fullPage: true });
  await closeTop();
  await page.getByRole('button', { name: '准备分析报告', exact: true }).click();
  await page.getByLabel('报告名称', { exact: true }).fill('合成105人精确整数回归报告');
  const reportFindings = page
    .getByRole('region', { name: '分析核验', exact: true })
    .locator('[data-finding-id]');
  for (const item of await reportFindings.all()) {
    if ((await item.getAttribute('data-finding-id')) !== chosen.discovery_id) {
      if ((await item.getAttribute('open')) === null) await item.locator('summary').click();
      await item
        .getByRole('radio', { name: '不加入报告', exact: true })
        .locator('xpath=ancestor::label[1]')
        .click();
    }
  }
  const reviewedResponse = wait(
    (r) => r.request().method() === 'PATCH' && /\/analysis-results\//.test(r.url()),
  );
  const savedResponse = wait(
    (r) => r.url().endsWith('/analysis-results') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '保存报告', exact: true }).click();
  const saved = await (await savedResponse).json();
  assert.equal(saved.analysis.members.length, 105);
  const selectedSaved = saved.discovery_findings.find(
    (f) => f.discovery.discovery_id === chosen.discovery_id,
  );
  assert.ok(selectedSaved);
  assert.deepEqual(saved.analysis.discovery.spec, spec);
  assert.deepEqual(
    saved.analysis.discovery.findings.find((f) => f.discovery_id === chosen.discovery_id).ranking,
    detailResult.body.ranking,
  );
  assert.deepEqual(selectedSaved.discovery.ranking, detailResult.body.ranking);
  const review = page.getByRole('region', { name: '分析核验', exact: true });
  const reviewed = await (await reviewedResponse).json();
  assert.equal(reviewed.version, 2);
  assert.deepEqual(
    reviewed.discovery_findings
      .filter((f) => f.teacher_review.status === 'confirmed')
      .map((f) => f.discovery.discovery_id),
    [chosen.discovery_id],
  );
  await review.locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
  // Refresh and reopen via the saved list, exercising a fresh GET and the nested DTO.
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.live-workspace').waitFor();
  await page.getByRole('button', { name: '已保存分析', exact: true }).click();
  const reopenedResponse = wait(
    (r) =>
      r.request().method() === 'GET' && new URL(r.url()).pathname.endsWith('/' + saved.result_id),
  );
  await page
    .locator('.saved-analysis-list .panel')
    .filter({ hasText: saved.title })
    .getByRole('button', { name: '查看与核验', exact: true })
    .click();
  const reopened = await (await reopenedResponse).json();
  assert.equal(reopened.result_id, saved.result_id);
  assert.equal(reopened.version, reviewed.version);
  assert.deepEqual(reopened.analysis.discovery, reviewed.analysis.discovery);
  assert.deepEqual(reopened.discovery_findings, reviewed.discovery_findings);
  assert.deepEqual(
    reopened.discovery_findings.find((f) => f.discovery.discovery_id === chosen.discovery_id)
      .discovery.ranking,
    detailResult.body.ranking,
  );
  await review.locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
  assert.equal(
    await review
      .locator(`[data-finding-id="${chosen.discovery_id}"]`)
      .getByRole('radio', { name: '已确认', exact: true })
      .isChecked(),
    true,
  );
  await page.screenshot({
    path: path.join(artifacts, '105-confirmed-reopened.png'),
    fullPage: true,
  });
  const downloads = [];
  for (const format of ['xlsx', 'md']) {
    const initialCheck = wait((r) => r.url().endsWith('/export-check'));
    await review.getByRole('button', { name: '导出测评报告', exact: true }).click();
    let preflight = await (await initialCheck).json();
    const dialog = page.getByRole('dialog', { name: '导出测评报告', exact: true });
    if (format === 'md') {
      const changed = wait(
        (r) => r.url().endsWith('/export-check') && r.request().postDataJSON().format === 'md',
      );
      await dialog.getByText('Markdown', { exact: true }).click();
      preflight = await (await changed).json();
    }
    assert.equal(preflight.member_count, 105);
    assert.equal(preflight.confirmed_finding_count, 1);
    const ack = dialog.getByRole('checkbox');
    if (await ack.count()) await ack.check();
    const download = page.waitForEvent('download');
    await dialog
      .getByRole('button', { name: format === 'md' ? '下载 Markdown' : '下载 Excel', exact: true })
      .click();
    const file = await download;
    const destination = path.join(artifacts, `105-confirmed-report.${format}`);
    await file.saveAs(destination);
    const bytes = await readFile(destination);
    const verified = verifyReportArtifact({
      format,
      bytes,
      destination,
      python,
      env,
      reference: {
        savedResult: reopened,
        batchId: batch.batch_id,
        snapshotId: batch.snapshot_id,
        resultId: saved.result_id,
        version: reviewed.version,
        studentCount: 105,
        discoveryIds: [chosen.discovery_id],
        factRows:
          detailResult.body.type === 'dimension_relation'
            ? [
                ['分析范围人数', detailResult.body.metrics.student_denominator],
                ['有效成对人数', detailResult.body.metrics.paired_count],
                ['等级相关系数', detailResult.body.metrics.rho.value.toFixed(2)],
              ]
            : [],
      },
    });
    downloads.push({ format, bytes: bytes.length, reopened: true, ...verified });
    await dialog.locator('.ant-modal-close').click();
  }
  await navigation('学情诊断');
  await focus.getByTestId('discovery-card').first().waitFor();
  await page.screenshot({ path: path.join(artifacts, '105-after-fix.png'), fullPage: true });
  await writeFile(
    path.join(artifacts, 'acceptance-record.json'),
    JSON.stringify(
      {
        ...record,
        status: control ? 'control_complete' : 'complete',
        product_accepted: !control,
        downloads,
      },
      null,
      2,
    ),
  );
  return [
    `${control ? 'CONTROL ONLY, not the precision fix: ' : ''}105/525 real upload → diagnosis → discovery/chart → exact original group → evidence → save → confirmed → refresh/reopen → validated MD/XLSX metadata`,
  ];
}
