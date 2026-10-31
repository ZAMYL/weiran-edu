import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

export async function teacherIntegerFlow(h) {
  const { page, origin, artifacts, navigation, importThroughPage, post, closeTop, python, env } = h;
  const wait = (predicate) =>
    page.waitForResponse(async (response) => {
      if (!predicate(response) || (await response.finished())) return false;
      const body = await response.json();
      response.json = async () => body;
      return true;
    });
  await page.goto(origin);
  await page.locator('.live-workspace').waitFor();
  const imported = await importThroughPage('precision105', '合成105人525评分精度验收');
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
  const record = {
    synthetic_only: true,
    frontend_revision: process.env.EDU_TEST_FRONTEND_REVISION || null,
    backend_revision: process.env.EDU_TEST_BACKEND_REVISION || null,
    backend_upload_revision: process.env.EDU_TEST_UPLOAD_REVISION || null,
    students: 105,
    score_rows: 525,
    discoveries_http_status: 200,
    detail_http_status: 200,
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
  await focus.getByTestId('discovery-card').waitFor();
  assert.equal(await focus.locator('.live-error').count(), 0);
  await focus.getByRole('button', { name: '查看发现与依据', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '发现详情', exact: true });
  await page.getByTestId('discovery-chart').locator('svg').waitFor();
  await drawer.getByRole('button', { name: '继续探索这组学生', exact: true }).click();
  await page.waitForURL('**/explorer?discovery=*');
  const pointer = JSON.parse(new URL(page.url()).searchParams.get('discovery'));
  assert.deepEqual(pointer.spec, spec);
  assert.equal(pointer.discovery_id, chosen.discovery_id);
  await page.locator('.match-summary').filter({ hasText: '/ 105' }).waitFor();
  const proofResponse = wait((r) => /\/evidence\/[^/?]+\?/.test(r.url()) && r.status() === 200);
  await page
    .getByRole('button', { name: /分证据$/ })
    .first()
    .click();
  const proof = await (await proofResponse).json();
  assert.equal(proof.batch_id, batch.batch_id);
  assert.equal(proof.snapshot_id, batch.snapshot_id);
  assert.equal(proof.comment, null);
  await closeTop();
  await page.getByRole('button', { name: '准备分析报告', exact: true }).click();
  await page.getByRole('button', { name: '保存本次分析', exact: true }).click();
  await page.getByLabel('分析名称', { exact: true }).fill('合成105人精确整数回归报告');
  const savedResponse = wait(
    (r) => r.url().endsWith('/analysis-results') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: '保存分析', exact: true }).click();
  const saved = await (await savedResponse).json();
  assert.equal(saved.analysis.members.length, 105);
  assert.equal(saved.discovery_findings.length, 1);
  assert.equal(saved.discovery_findings[0].discovery.discovery_id, chosen.discovery_id);
  assert.deepEqual(saved.analysis.discovery.spec, spec);
  const review = page.getByRole('region', { name: '分析核验', exact: true });
  await review.locator('.saved-version').waitFor();
  await review
    .getByRole('radio', { name: '已确认', exact: true })
    .locator('xpath=ancestor::label[1]')
    .click();
  const reviewedResponse = wait(
    (r) => r.url().endsWith('/' + saved.result_id) && r.request().method() === 'PATCH',
  );
  await review.getByRole('button', { name: '保存核验结果', exact: true }).click();
  const reviewed = await (await reviewedResponse).json();
  assert.equal(reviewed.version, 2);
  assert.equal(reviewed.discovery_findings[0].teacher_review.status, 'confirmed');
  await review.locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
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
      .getByRole('button', { name: format === 'md' ? '下载 Markdown' : '下载 XLSX', exact: true })
      .click();
    const file = await download;
    const destination = path.join(artifacts, `105-confirmed-report.${format}`);
    await file.saveAs(destination);
    const bytes = await readFile(destination);
    if (format === 'md') assert.match(bytes.toString('utf8'), /^#/);
    else {
      assert.equal(bytes.subarray(0, 4).toString('hex'), '504b0304');
      execFileSync(
        python,
        [
          '-c',
          'import sys; from openpyxl import load_workbook; b=load_workbook(sys.argv[1], read_only=True, data_only=True); assert b.sheetnames; b.close()',
          destination,
        ],
        { env, windowsHide: true },
      );
    }
    downloads.push({ format, bytes: bytes.length, reopened: true });
    await dialog.locator('.ant-modal-close').click();
  }
  await navigation('学情诊断');
  await focus.getByTestId('discovery-card').waitFor();
  await page.screenshot({ path: path.join(artifacts, '105-after-fix.png'), fullPage: true });
  await writeFile(
    path.join(artifacts, 'acceptance-record.json'),
    JSON.stringify(
      {
        ...record,
        status: 'complete',
        product_accepted: true,
        downloads,
      },
      null,
      2,
    ),
  );
  return [
    '105/525 real upload → diagnosis → discovery/chart → exact original group → evidence → save → confirmed → reopened MD/XLSX',
  ];
}
