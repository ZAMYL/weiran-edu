import assert from 'node:assert/strict';
import path from 'node:path';

export async function teacherDiscoveryCopyFlow({
  page,
  origin,
  artifacts,
  temporary,
  navigation,
  importThroughPage,
  post,
  closeTop,
}) {
  const focus = () => page.locator('.discoveries-panel');
  const waitFocus = () => focus().getByTestId('discovery-card').first().waitFor();
  const clean = async () => {
    assert.doesNotMatch(await focus().innerText(), /互斥群体|目标组|余组|待核查|需谨慎解释/);
    assert.equal(await focus().locator('.ant-tag-gold').count(), 0);
    assert.doesNotMatch(await page.title(), /[。.]$/);
    assert.equal(
      await page.getByRole('button', { name: /AI.*调查|自主调查|Qwen|工具轨迹/ }).count(),
      0,
    );
  };
  await page.goto(origin);
  await page.locator('.live-workspace').waitFor();
  const imported = await importThroughPage('teacher53', '合成教师表达验收53人');
  await waitFocus();
  const spec = {
    snapshot_id: imported.data.scope.snapshot_id,
    profile: 'edu.discovery.profile.v1',
    scope_conditions: [],
  };
  const summary = imported.data.items.find((d) => d.type === 'group_difference');
  assert.ok(summary);
  const result = await post(
    `/batches/${imported.batchId}/discoveries/${summary.discovery_id}/query`,
    spec,
  );
  assert.equal(result.status, 200);
  const d = result.body;
  assert.equal(d.metrics.student_denominator, 53);
  assert.equal(d.metrics.target_count, 15);
  assert.equal(d.metrics.remainder_count, 38);
  const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension);
  assert.equal(d.metrics.outcome_dimension, 'code_implementation');
  assert.equal(row.target.mean.numerator / row.target.mean.denominator, 3.69);
  assert.equal(row.remainder.mean.numerator / row.remainder.mean.denominator, 3);
  assert.equal(row.difference.numerator / row.difference.denominator, 0.69);
  await focus().getByRole('combobox', { name: '查看重点发现', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText('群体差异', { exact: true }).click();
  const card = focus().getByTestId('discovery-card');
  assert.equal(await card.getAttribute('data-discovery-id'), summary.discovery_id);
  assert.equal(
    await card.getByRole('heading', { level: 3 }).innerText(),
    '符合条件的学生，代码实现能力评分较高',
  );
  assert.equal(
    await card.locator('.focus-grouping').innerText(),
    '分组依据：算法与数据结构 ≤ 3.49 分',
  );
  assert.deepEqual(await card.locator('.focus-metrics strong').allTextContents(), [
    '3.69',
    '3.00',
    '+0.69',
  ]);
  assert.match(await card.locator('.focus-metrics dt').first().innerText(), /15 人/);
  assert.match(await card.locator('.focus-metrics dt').nth(1).innerText(), /38 人/);
  await card.locator('.focus-explanation > summary').click();
  assert.doesNotMatch(
    await card.locator('.focus-explanation').innerText(),
    /15|38|3\.69|3\.00|0\.69/,
  );
  await card.locator('.focus-explanation > summary').click();
  await clean();
  for (const [width, height] of [
    [1920, 1080],
    [1440, 900],
    [390, 844],
  ]) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(300);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
      false,
    );
    if (width > 960) {
      const a = await page.locator('.radar-panel').boundingBox();
      const b = await page.locator('.insights-right').boundingBox();
      assert.ok(Math.abs(a.height - b.height) <= 1);
    }
    await page.screenshot({
      path: path.join(artifacts, `teacher-discovery-53-${width}.png`),
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await card.getByRole('button', { name: '查看发现与依据', exact: true }).click();
  const drawer = page.locator('.ant-drawer-open').last();
  await drawer.locator('.discovery-evidence-notes').waitFor();
  const notes = await drawer.locator('.discovery-evidence-notes').innerText();
  assert.ok(notes.includes(`支持样本 ${d.ranking.support_count} 人`));
  assert.ok(notes.includes(`计划 ${d.ranking.robustness.planned} 次`));
  assert.ok(notes.includes(`完成 ${d.ranking.robustness.evaluated} 次`));
  assert.ok(notes.includes(`符合检查规则 ${d.ranking.robustness.passed} 次`));
  const membersResponse = page.waitForResponse(
    (r) => r.url().endsWith('/records/query') && r.status() === 200,
  );
  await drawer.getByRole('button', { name: '符合条件的学生', exact: true }).click();
  const members = await (await membersResponse).json();
  assert.equal(members.total_count, 15);
  assert.equal(members.scope.snapshot_id, spec.snapshot_id);
  await page.getByTestId('discovery-record-count').waitFor();
  await closeTop();
  await closeTop();
  await card.getByRole('checkbox').check();
  await navigation('分析报告');
  await page.getByText('符合条件的学生，代码实现能力评分较高', { exact: true }).waitFor();
  await navigation('学情诊断');
  await waitFocus();
  await focus().getByRole('combobox', { name: '查看重点发现', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText('群体差异', { exact: true }).click();
  await card.getByRole('button', { name: '继续探索这组学生', exact: true }).click();
  await page.waitForURL('**/explorer?discovery=*');
  const pointer = JSON.parse(new URL(page.url()).searchParams.get('discovery'));
  assert.equal(pointer.batch_id, imported.batchId);
  assert.equal(pointer.discovery_id, summary.discovery_id);
  assert.equal(pointer.metric_key, 'target_members');
  assert.deepEqual(pointer.spec, spec);
  await page.locator('.match-summary').filter({ hasText: '15 / 53' }).waitFor();
  assert.match(await page.locator('.match-summary').innerText(), /≤ 3\.49/);

  // A real <= filter asks the existing backend for its boundary comparison.
  await page.getByRole('button', { name: '添加条件', exact: true }).click();
  await page.getByRole('combobox', { name: '筛选条件1比较', exact: true }).click();
  await page.locator('.ant-select-dropdown:visible').getByText('≤', { exact: true }).click();
  await page.getByRole('textbox', { name: '筛选条件1评分', exact: true }).fill('3.49');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await navigation('学情诊断');
  await waitFocus();
  const boundarySpec = {
    ...spec,
    scope_conditions: [
      { dimension_code: 'algorithm_data_structure', operator: 'lte', value: 3.49 },
    ],
  };
  const boundaryList = await post(`/batches/${imported.batchId}/discoveries/query`, boundarySpec);
  const boundary = boundaryList.body.items.find((item) => item.type === 'boundary_sensitivity');
  assert.ok(boundary);
  const boundaryDetail = await post(
    `/batches/${imported.batchId}/discoveries/${boundary.discovery_id}/query`,
    boundarySpec,
  );
  assert.equal(boundaryDetail.status, 200);
  assert.equal(boundaryDetail.body.metrics.added_count, 15);
  assert.equal(await card.getAttribute('data-discovery-id'), boundary.discovery_id);
  assert.match(await card.getByRole('heading', { level: 3 }).innerText(), /多选出 15 名学生/);
  await card.locator('.focus-explanation > summary').click();
  assert.match(await card.locator('.focus-explanation').innerText(), /不是新增教学诊断/);
  await clean();
  await page.screenshot({
    path: path.join(artifacts, 'teacher-discovery-boundary.png'),
    fullPage: true,
  });

  const rare = await importThroughPage('rare', '合成17人单个少见组合');
  await waitFocus();
  const small = rare.data.items.filter((item) => item.type === 'rare_combination');
  assert.ok(small.length > 0);
  for (const item of small) {
    const detail = await post(`/batches/${rare.batchId}/discoveries/${item.discovery_id}/query`, {
      ...spec,
      snapshot_id: rare.data.scope.snapshot_id,
    });
    assert.equal(detail.status, 200);
    assert.equal(detail.body.metrics.match_count, 1);
    assert.equal(await focus().locator(`[data-discovery-id="${item.discovery_id}"]`).count(), 0);
  }
  await clean();
  await navigation('测评数据');
  const checked = page.waitForResponse(
    (r) => r.url().endsWith('/imports/preview') && r.status() === 201,
  );
  await page.locator('input[type=file]').setInputFiles(path.join(temporary, 'missing.xlsx'));
  const missing = await (await checked).json();
  assert.ok(missing.summary.error_count > 0);
  await page.getByText('存在阻断错误，无法确认入库', { exact: true }).waitFor();
  assert.equal(
    await page.getByRole('button', { name: '下一步：确认导入', exact: true }).isDisabled(),
    true,
  );
  await page.screenshot({
    path: path.join(artifacts, 'teacher-discovery-missing-score.png'),
    fullPage: true,
  });
  await page.getByRole('button', { name: '更换文件', exact: true }).click();

  const tiny = await importThroughPage('empty', '合成1人小样本');
  assert.equal(tiny.data.scope.batch_record_count, 1);
  await focus().getByText('当前范围没有符合条件的发现', { exact: true }).waitFor();
  assert.equal(await focus().getByTestId('discovery-card').count(), 0);
  const zero = await importThroughPage('constant', '合成17人零发现');
  assert.equal(zero.data.retained_count, 0);
  await focus().getByText('当前范围没有符合条件的发现', { exact: true }).waitFor();
  assert.equal(await focus().getByTestId('discovery-card').count(), 0);
  await page.screenshot({
    path: path.join(artifacts, 'teacher-discovery-zero-17.png'),
    fullPage: true,
  });
  return [
    '53 students: real 15/38 groups, 3.69/3.00 means, +0.69 difference, actual grouping condition, plain title and no normal warning badge',
    'Original group/snapshot replay and report selection preserved; readable evidence limits and actual stability counts',
    'Real boundary filter and relation cards; rare one-student combinations excluded from priority; one-student and 17-student zero findings; missing Excel score blocked by real API',
    '1920/1440/390 screenshots, desktop equal panel heights and no horizontal overflow',
  ];
}
