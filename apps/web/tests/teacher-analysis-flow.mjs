// Runs inside live-api-smoke's isolated synthetic API/SQLite and teacher session.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

async function reportAction(region, name) {
  const button = region.getByRole('button', { name, exact: true });
  if (!(await button.isVisible())) await region.locator('.technical-details > summary').click();
  await button.click();
}

async function checkDecisionAlignment(region) {
  await region
    .locator('.report-decision-options')
    .first()
    .locator('.ant-radio-button-wrapper')
    .first()
    .scrollIntoViewIfNeeded();
  const boxes = await region
    .locator('.report-decision-options')
    .first()
    .locator('.ant-radio-button-wrapper')
    .evaluateAll((elements) =>
      elements.map((element) => {
        const box = element.getBoundingClientRect();
        return {
          y: box.y,
          width: box.width,
          height: box.height,
          margin: getComputedStyle(element).marginTop,
        };
      }),
    );
  assert.equal(boxes.length, 2);
  assert.ok(
    boxes.every(
      (box) =>
        Math.abs(box.y - boxes[0].y) < 1 &&
        // Radio borders and fractional grid widths can differ by one CSS pixel.
        Math.abs(box.width - boxes[0].width) <= 1 &&
        Math.abs(box.height - 44) < 1 &&
        box.margin === '0px',
    ),
    JSON.stringify(boxes),
  );
}

export async function teacherAnalysisFlow({
  page,
  batch,
  spec,
  request,
  admin,
  scope,
  switchSession,
  ownerToken,
  shot,
  navigation,
}) {
  const checks = [];
  const base = `/batches/${batch.batch_id}/analysis-results`;
  const drawer = () => page.getByRole('region', { name: '分析核验', exact: true });
  const exportDialog = () => page.getByRole('dialog', { name: '导出测评报告', exact: true });
  const downloads = [];
  const onDownload = (download) => downloads.push(download);
  page.on('download', onDownload);
  const acknowledge = async () => {
    const checkbox = exportDialog().getByRole('checkbox', {
      name: '我已了解以上提醒，按当前核验结果导出',
    });
    if (await checkbox.count()) await checkbox.check();
  };
  const openExport = async () => {
    const response = page.waitForResponse(
      (r) => r.url().endsWith('/export-check') && r.request().method() === 'POST',
    );
    await drawer().getByRole('button', { name: '导出测评报告', exact: true }).click();
    const check = await (await response).json();
    await exportDialog().getByText('报告预估大小', { exact: false }).waitFor();
    return check;
  };
  const closeExport = () => exportDialog().locator('.ant-modal-close').click();
  try {
    const labels = await page
      .locator('.live-candidate .finding-top > :is(.ant-tag, small)')
      .evaluateAll((elements) =>
        elements.map((element) => ({
          font: parseFloat(getComputedStyle(element).fontSize),
          line: parseFloat(getComputedStyle(element).lineHeight),
          height: element.getBoundingClientRect().height,
        })),
      );
    assert.ok(
      labels.length > 0 &&
        labels.every(
          (label) => label.font === 12 && label.line >= label.font && label.height >= label.line,
        ),
    );
    const created = await request(base, {
      method: 'POST',
      body: JSON.stringify({ ...spec, title: '合成教师闭环分析', note: '合成分析备注' }),
    });
    await page.getByRole('button', { name: '已保存分析', exact: true }).click();
    await page.getByRole('button', { name: '查看与核验', exact: true }).first().click();
    assert.equal(created.analysis.matched_count, 12);
    assert.equal(created.version, 1);
    await drawer().locator('.review-finding').first().waitFor();
    await checkDecisionAlignment(drawer());
    assert.equal(await drawer().locator('.review-finding').count(), created.findings.length);
    assert.equal(
      await drawer().locator('.review-finding input[value=confirmed]').first().isChecked(),
      true,
    );
    await drawer().locator('.review-finding').nth(1).locator('summary').first().click();
    await drawer()
      .locator('.review-finding')
      .nth(1)
      .getByRole('radio', { name: '不加入报告', exact: true })
      .locator('xpath=ancestor::label[1]')
      .click();
    await drawer().locator('.review-finding').nth(0).locator('summary').click();
    await drawer().locator('.review-finding').nth(0).locator('textarea').fill('合成核验备注');
    assert.equal(
      await drawer().getByRole('button', { name: '导出测评报告', exact: true }).isDisabled(),
      true,
    );
    const updatedHTTP = page.waitForResponse(
      (r) => r.url().endsWith(`/${created.result_id}`) && r.request().method() === 'PATCH',
    );
    await drawer().getByRole('button', { name: '保存核验结果', exact: true }).click();
    const updatedResponse = await updatedHTTP;
    assert.equal(updatedResponse.status(), 200);
    const posted = updatedResponse.request().postDataJSON();
    assert.equal(posted.expected_version, 1);
    assert.equal(posted.snapshot_id, batch.snapshot_id);
    assert.equal(posted.decisions.length, created.findings.length);
    assert.deepEqual(Object.keys(posted).sort(), [
      'decisions',
      'expected_version',
      'note',
      'snapshot_id',
      'title',
    ]);
    const reviewed = await updatedResponse.json();
    await drawer().locator('.saved-version').filter({ hasText: '已保存版本 2' }).waitFor();
    const stored = await request(`${base}/${created.result_id}?snapshot_id=${batch.snapshot_id}`);
    assert.equal(stored.version, 2);
    assert.equal(stored.findings[0].teacher_review.status, 'confirmed');
    assert.equal(stored.findings[1].teacher_review.status, 'dismissed');
    assert.ok(stored.findings.slice(2).every((f) => f.teacher_review.status === 'confirmed'));
    await shot('analysis-review-desktop');
    const evidenceHTTP = page.waitForResponse((r) => r.url().endsWith('/records/query'));
    await drawer()
      .getByRole('button', { name: /查看评分依据/ })
      .first()
      .click();
    assert.deepEqual((await evidenceHTTP).request().postDataJSON().conditions, spec.conditions);
    await page.locator('.ant-drawer-open .ant-drawer-close').last().click();
    checks.push(
      'save replays applied query only; pending/confirmed/dismissed decisions persist with version; evidence uses saved query; card labels stay 12px',
    );

    const preflight = await openExport();
    assert.doesNotMatch(await exportDialog().innerText(), /班级|全班|课堂成绩|算法比赛/);
    assert.equal(preflight.result_version, 2);
    assert.equal(preflight.member_count, 12);
    assert.equal(preflight.confirmed_finding_count, created.findings.length - 1);
    assert.ok(!preflight.warnings.some((w) => w.code === 'PENDING_FINDINGS_EXCLUDED'));
    assert.ok(preflight.warnings.some((w) => w.code === 'DISMISSED_FINDINGS_EXCLUDED'));
    assert.equal(
      await exportDialog().getByRole('button', { name: '下载 XLSX', exact: true }).isDisabled(),
      true,
    );
    await acknowledge();
    const xlsxDownload = page.waitForEvent('download');
    await exportDialog().getByRole('button', { name: '下载 XLSX', exact: true }).click();
    const xlsx = await xlsxDownload;
    assert.equal(xlsx.suggestedFilename(), `weiran-analysis-${created.result_id}-v2.xlsx`);
    const xlsxBytes = await readFile(await xlsx.path());
    assert.deepEqual([...xlsxBytes.subarray(0, 4)], [0x50, 0x4b, 0x03, 0x04]);
    const mdCheck = page.waitForResponse(
      (r) => r.url().endsWith('/export-check') && r.request().postDataJSON().format === 'md',
    );
    await exportDialog().getByText('Markdown', { exact: true }).click();
    await mdCheck;
    assert.equal(await exportDialog().getByRole('checkbox').isChecked(), false);
    await acknowledge();
    const mdDownload = page.waitForEvent('download');
    await exportDialog().getByRole('button', { name: '下载 Markdown', exact: true }).click();
    const md = await mdDownload;
    assert.equal(md.suggestedFilename(), `weiran-analysis-${created.result_id}-v2.md`);
    const text = await readFile(await md.path(), 'utf8');
    assert.match(text, /^#/);
    assert.ok(!text.includes('合成核验备注') && !text.includes('合成分析备注'));
    await shot('analysis-export-desktop');
    await closeExport();
    checks.push(
      'actual XLSX and Markdown download after explicit warnings; switching format resets acknowledgement; free notes excluded',
    );

    const mutate = (version, note) =>
      request(`${base}/${created.result_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ snapshot_id: batch.snapshot_id, expected_version: version, note }),
      });
    await mutate(2, '合成并发修改');
    await drawer().getByLabel('分析备注', { exact: true }).fill('合成本地未保存修改');
    await drawer().getByRole('button', { name: '保存核验结果', exact: true }).click();
    await drawer()
      .locator('details')
      .filter({ hasText: '错误码：ANALYSIS_RESULT_VERSION_MISMATCH' })
      .locator('summary')
      .click();
    await drawer().getByText('错误码：ANALYSIS_RESULT_VERSION_MISMATCH', { exact: true }).waitFor();
    assert.equal(
      await drawer().getByLabel('分析备注', { exact: true }).inputValue(),
      '合成本地未保存修改',
    );
    await reportAction(drawer(), '加载最新版本');
    await page.getByRole('button', { name: '放弃修改', exact: true }).click();
    await drawer().locator('.saved-version').filter({ hasText: '已保存版本 3' }).waitFor();
    await openExport();
    await acknowledge();
    await mutate(3, '合成预检后并发修改');
    const beforeConflict = downloads.length;
    await exportDialog().getByRole('button', { name: '下载 XLSX', exact: true }).click();
    await exportDialog()
      .locator('details')
      .filter({ hasText: '错误码：ANALYSIS_RESULT_VERSION_MISMATCH' })
      .locator('summary')
      .click();
    await exportDialog()
      .getByText('错误码：ANALYSIS_RESULT_VERSION_MISMATCH', { exact: true })
      .waitFor();
    assert.equal(downloads.length, beforeConflict);
    await exportDialog().getByRole('button', { name: '关闭并加载最新结果' }).click();
    await drawer().locator('.saved-version').filter({ hasText: '已保存版本 4' }).waitFor();
    checks.push(
      'actual save conflict preserves local changes; actual version race after preflight yields 409 and no download',
    );

    await openExport();
    await acknowledge();
    admin('member-remove', '--teacher-id', reviewed.reviewed_by, '--scope-id', scope);
    const beforeRevoked = downloads.length;
    await exportDialog().getByRole('button', { name: '下载 XLSX', exact: true }).click();
    await exportDialog().locator('.live-error').waitFor();
    assert.equal(downloads.length, beforeRevoked);
    admin('member-set', '--teacher-id', reviewed.reviewed_by, '--scope-id', scope, '--can-import');
    await exportDialog().getByRole('button', { name: '关闭并加载最新结果' }).click();
    await drawer().locator('.saved-version').filter({ hasText: '已保存版本 4' }).waitFor();
    checks.push('actual permission revocation after successful preflight prevents report download');

    // A reader uses the existing session mechanism, never a new login system.
    const reader = admin('teacher-create', '--subject', 'synthetic-analysis-reader').teacher_id;
    admin('member-set', '--teacher-id', reader, '--scope-id', scope);
    admin('batch-grant-read', '--teacher-id', reader, '--batch-id', batch.batch_id);
    const readerToken = admin('token-issue', '--teacher-id', reader, '--hours', '1').access_token;
    await switchSession(readerToken);
    await navigation('学情诊断');
    await page.getByRole('button', { name: '已保存分析', exact: true }).waitFor();
    assert.equal(
      await page.getByRole('button', { name: '准备分析报告', exact: true }).isDisabled(),
      true,
    );
    await page.getByRole('button', { name: '已保存分析', exact: true }).click();
    await page.getByRole('button', { name: '查看与核验', exact: true }).first().click();
    await drawer()
      .getByText('当前为只读权限，可查看核验结果和导出测评报告。', { exact: true })
      .waitFor();
    assert.equal(
      await drawer().getByRole('button', { name: '保存核验结果', exact: true }).count(),
      0,
    );
    assert.equal(await drawer().getByLabel('报告名称', { exact: true }).isDisabled(), true);
    await openExport();
    await acknowledge();
    const readerDownload = page.waitForEvent('download');
    await exportDialog().getByRole('button', { name: '下载 XLSX', exact: true }).click();
    assert.match((await readerDownload).suggestedFilename(), /-v4\.xlsx$/);
    checks.push(
      'authorized reader can list/read/export saved results but cannot save or edit judgments',
    );
    await switchSession(ownerToken);
    await navigation('学情诊断');
    // Pagination comes from the server; each item is a real saved aggregate.
    for (let index = 0; index < 10; index++)
      await request(base, {
        method: 'POST',
        body: JSON.stringify({ ...spec, title: `合成分页分析 ${index + 1}` }),
      });
    await page.getByRole('button', { name: '已保存分析', exact: true }).click();
    await page.getByText('共 11 次分析', { exact: true }).waitFor();
    const secondPageHTTP = page.waitForResponse(
      (r) =>
        r.url().includes('/analysis-results?') &&
        new URL(r.url()).searchParams.get('offset') === '10',
    );
    await page.locator('.report-surface .ant-pagination').getByTitle('2', { exact: true }).click();
    const secondPage = await (await secondPageHTTP).json();
    assert.equal(secondPage.page.returned, 1);
    await page.waitForFunction(
      () => document.querySelectorAll('.saved-analysis-list .panel').length === 1,
    );
    await page.getByRole('button', { name: '查看与核验', exact: true }).click();
    await drawer()
      .locator('.saved-version')
      .filter({ hasText: `已保存版本 ${secondPage.items[0].version}` })
      .waitFor();
    assert.equal(
      await drawer().getByLabel('报告名称', { exact: true }).inputValue(),
      secondPage.items[0].title,
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => {
      const panel = document.querySelector('.report-surface');
      const bounds = panel?.getBoundingClientRect();
      return (
        bounds &&
        bounds.left >= 0 &&
        bounds.right <= innerWidth + 1 &&
        panel.scrollWidth <= panel.clientWidth + 1
      );
    });
    await checkDecisionAlignment(drawer());
    await shot('analysis-review-mobile');
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    await reportAction(drawer(), '返回报告列表');
    await page.setViewportSize({ width: 1440, height: 1000 });
    checks.push('saved analysis server pagination and 390px review drawer');
    await navigation('画像探索');
    await page.getByRole('button', { name: '添加条件', exact: true }).click();
    assert.equal(
      await page.getByRole('button', { name: '准备分析报告', exact: true }).isDisabled(),
      true,
    );
    await page.getByRole('combobox', { name: '筛选条件1比较' }).click();
    await page.locator('.ant-select-dropdown').getByText('=', { exact: true }).click();
    await page.getByRole('button', { name: '添加条件', exact: true }).click();
    await page.getByRole('textbox', { name: '筛选条件2评分' }).fill('3.25');
    await page.getByRole('button', { name: '应用筛选', exact: true }).click();
    await page.locator('.match-summary').filter({ hasText: '12 / 23' }).waitFor();
    await navigation('学情诊断');
    checks.push('explorer blocks saving unapplied filter drafts');
  } finally {
    page.off('download', onDownload);
  }
  return checks;
}

export async function saveEmptyAnalysis(page, title, expectedMatched, expectedNoFindings) {
  const createdHTTP = page.waitForResponse(
    (r) => r.url().endsWith('/analysis-results') && r.request().method() === 'POST',
  );
  if (!new URL(page.url()).pathname.startsWith('/reports'))
    await page.getByRole('button', { name: '准备分析报告', exact: true }).click();
  await page.getByLabel('报告名称', { exact: true }).fill(title);
  await page.getByRole('button', { name: '保存报告', exact: true }).click();
  const response = await createdHTTP;
  assert.equal(response.status(), 201);
  const result = await response.json();
  assert.equal(result.analysis.matched_count, expectedMatched);
  const drawer = page.getByRole('region', { name: '分析核验', exact: true });
  await drawer.locator('.saved-version').waitFor();
  if (expectedNoFindings) {
    assert.equal(result.findings.length, 0);
    await drawer
      .getByText('本次分析没有保存候选发现，仍可导出测评报告。', { exact: true })
      .waitFor();
    const preflightHTTP = page.waitForResponse((r) => r.url().endsWith('/export-check'));
    await drawer.getByRole('button', { name: '导出测评报告', exact: true }).click();
    assert.equal((await (await preflightHTTP).json()).confirmed_finding_count, 0);
    const dialog = page.getByRole('dialog', { name: '导出测评报告', exact: true });
    await dialog.getByText('报告预估大小', { exact: false }).waitFor();
    if (await dialog.getByRole('checkbox').count()) await dialog.getByRole('checkbox').check();
    const pendingDownload = page.waitForEvent('download');
    await dialog.getByRole('button', { name: '下载 XLSX', exact: true }).click();
    assert.equal(
      (await pendingDownload).suggestedFilename(),
      `weiran-analysis-${result.result_id}-v1.xlsx`,
    );
    await dialog.locator('.ant-modal-close').click();
  }
  await reportAction(drawer, '返回报告列表');
}
