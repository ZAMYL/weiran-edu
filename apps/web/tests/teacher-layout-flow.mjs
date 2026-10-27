import assert from 'node:assert/strict';
import path from 'node:path';
import { writeFile } from 'node:fs/promises';
const sizes = [
  [1440, 900],
  [1920, 1080],
  [390, 844],
];
const capture = async (page, artifacts, name) => {
  for (const [width, height] of sizes) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => scrollTo(0, 0));
    await page.waitForTimeout(400);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
      `${name}: no document horizontal overflow at ${width}px`,
    );
    await page.screenshot({
      path: path.join(artifacts, `after-${name}-${width}x${height}.png`),
      fullPage: false,
    });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
};
export async function teacherLayoutFlow({
  page,
  origin,
  artifacts,
  navigation,
  importThroughPage,
  selectBatch,
  closeTop,
}) {
  const focus = () => page.locator('.discoveries-panel');
  const ready = async () => {
    await focus().getByTestId('discovery-card').first().waitFor();
    await page.waitForFunction(
      () => !document.querySelector('.discoveries-panel')?.textContent.includes('正在核查涉及人数'),
    );
  };
  const clean = async () =>
    assert.doesNotMatch(
      await page.locator('.workspace').innerText(),
      /Demo|联调|服务端|预览版本|算法比赛|班级|全班|105\.00|Spearman=/,
    );
  await page.goto(origin);
  await page.locator('.live-workspace').waitFor();
  await page.waitForURL('**/insights');
  assert.deepEqual(await page.locator('.main-nav a').allTextContents(), [
    '学情诊断',
    '画像探索',
    '分析报告',
  ]);
  await page.getByRole('heading', { name: '上传测评', exact: true }).waitFor();
  await clean();
  await capture(page, artifacts, 'data');
  const imported = await importThroughPage('contrast105', '合成竞赛测评 · 105人');
  assert.equal(imported.data.scope.batch_record_count, 105);
  await ready();
  assert.equal(await page.locator('.stat-grid strong').first().innerText(), '105');
  assert.ok((await focus().getByTestId('discovery-card').count()) <= 3);
  assert.equal(await page.locator('input[type=file]').count(), 0);
  await clean();
  await capture(page, artifacts, 'diagnosis');
  await page.screenshot({ path: path.join(artifacts, 'after-diagnosis-full.png'), fullPage: true });
  const layout = await page.evaluate(() => {
    const discover = document.querySelector('.discoveries-panel').getBoundingClientRect();
    const charts = document.querySelector('.insights-grid').getBoundingClientRect();
    const all = document.querySelector('.all-discoveries-panel').getBoundingClientRect();
    const right = document.querySelector('.insights-right > .panel');
    return {
      discoveryBottom: discover.bottom,
      chartTop: charts.top,
      allTop: all.top,
      chartsBottom: charts.bottom,
      discoveryWidth: discover.width,
      chartWidth: charts.width,
      rightScroll:
        ['scroll', 'auto'].includes(getComputedStyle(right).overflowY) &&
        right.scrollHeight > right.clientHeight + 1,
    };
  });
  assert.ok(
    layout.chartTop >= layout.discoveryBottom &&
      layout.allTop >= layout.chartsBottom &&
      Math.abs(layout.discoveryWidth - layout.chartWidth) <= 1,
  );
  assert.equal(layout.rightScroll, false);
  await focus().getByRole('button', { name: '查看发现与依据', exact: true }).first().click();
  await page.getByTestId('discovery-chart').locator('svg').waitFor();
  await page.getByRole('button', { name: '配对评分学生', exact: true }).click();
  await page.getByTestId('discovery-record-count').filter({ hasText: '105' }).waitFor();
  const proofResponse = page.waitForResponse(
    (r) => /\/evidence\/[^/?]+\?/.test(r.url()) && r.status() === 200,
  );
  proofResponse.catch(() => {});
  await page
    .locator('.ant-drawer-open')
    .last()
    .getByRole('button', { name: /评分证据$/ })
    .first()
    .click();
  const proof = await (await proofResponse).json();
  assert.equal(proof.batch_id, imported.batchId);
  assert.equal(proof.snapshot_id, imported.data.scope.snapshot_id);
  assert.equal(proof.comment, null);
  await capture(page, artifacts, '105-evidence');
  await closeTop();
  await closeTop();
  await closeTop();
  await focus().getByRole('button', { name: '进一步探索', exact: true }).first().click();
  await page.locator('.match-summary').filter({ hasText: '105 / 105' }).waitFor();
  assert.ok(new URL(page.url()).searchParams.has('discovery'));
  await navigation('学情诊断');
  await ready();
  await focus().getByRole('checkbox').first().check();
  await navigation('分析报告');
  await page.getByText('已加入待保存的报告 · 1 项发现', { exact: true }).waitFor();
  await capture(page, artifacts, 'report-draft');
  await navigation('学情诊断');
  await ready();
  assert.equal(await focus().getByRole('checkbox').first().isChecked(), true);
  const second = await importThroughPage('constant', '合成阶段测评 · 17人零发现');
  assert.notEqual(second.batchId, imported.batchId);
  assert.equal(second.data.scope.batch_record_count, 17);
  assert.equal(second.data.retained_count, 0);
  await focus()
    .getByText('当前范围没有符合条件的发现，可调整筛选后重试。', { exact: true })
    .waitFor();
  await page.locator('.stat-grid strong').filter({ hasText: /^17$/ }).waitFor();
  await capture(page, artifacts, 'zero-17');
  await navigation('分析报告');
  assert.equal(await page.getByText('已加入待保存的报告 · 1 项发现', { exact: true }).count(), 0);
  await navigation('学情诊断');
  await selectBatch('合成竞赛测评 · 105人');
  await ready();
  await page.reload();
  await page.locator('.live-workspace').waitFor();
  await page.locator('.stat-grid strong').filter({ hasText: /^105$/ }).waitFor();
  await ready();
  assert.equal(await page.locator('input[type=file]').count(), 0);
  await page.goto(origin);
  await page.waitForURL('**/insights');
  await page.locator('.stat-grid strong').filter({ hasText: /^105$/ }).waitFor();
  await ready();
  await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
  await page.getByRole('heading', { name: '历史测评', exact: true }).waitFor();
  await page.getByRole('row').filter({ hasText: '合成竞赛测评 · 105人' }).waitFor();
  await capture(page, artifacts, 'history');
  await writeFile(
    path.join(artifacts, 'layout-checks.json'),
    JSON.stringify(
      {
        sizes,
        layout,
        syntheticStudents: [105, 17],
        restoredBatchCheckedByBackend: true,
        reportDraftMemoryOnly: true,
      },
      null,
      2,
    ),
  );
  return [
    'No-data home uploads directly; confirmation opens diagnosis; second upload switches to 17-student zero findings',
    '105-student real findings/charts/owned evidence, controlled further exploration, memory-only report draft across navigation; batch change clears draft',
    'Current authorized batch survives reload and reopening even after a newer batch exists; history remains a secondary workspace entry',
    'Actual 1920x1080 / 1440x900 / 390x844 screenshots; no public development jargon or nested card scrolling',
  ];
}

teacherLayoutFlow.capture = capture;
