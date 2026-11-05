import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';

export async function questionApiFlow({
  page,
  origin,
  artifacts,
  temporary,
  python,
  env,
  importThroughPage,
}) {
  const file = path.join(temporary, '逐题测评.xlsx');
  execFileSync(
    python,
    [
      '-c',
      [
        'import sys',
        'from openpyxl import Workbook',
        'w=Workbook(); a=w.active; a.title="Sheet1"',
        'a.append(["edu-question-xlsx.v1","题目名称","最低分","最高分","步长","单位"])',
        'for j in range(1,14): a.append([f"Q{j:02d}",f"原题 {j}",0,j*10,0.01 if j%2 else 0.5,"分"])',
        'b=w.create_sheet("Sheet2"); b.append(["参评记录",*[f"Q{j:02d}" for j in range(1,14)]])',
        'for i in range(20): b.append([f"S{i:04d}",*[(i*3+j)%11/2 for j in range(1,14)]])',
        'b["B3"]=None; b["C4"]="invalid"; b["D5"]=999; b["E6"]=1.25',
        'w.save(sys.argv[1])',
      ].join('\n'),
      file,
    ],
    { env, windowsHide: true },
  );
  const checks = [];
  await page.goto(origin + '/insights');
  await page.getByRole('heading', { name: '选择 Excel 文件' }).waitFor();
  const previewResponse = page.waitForResponse(
    (r) => new URL(r.url()).pathname === '/api/edu/imports/preview' && r.status() === 201,
  );
  await page.locator('input[type=file]').setInputFiles(file);
  const preview = await (await previewResponse).json();
  assert.equal(preview.format, 'edu-question-xlsx.v1');
  assert.equal(preview.participant_count, 20);
  assert.equal(preview.observation_count, 260);
  assert.deepEqual(preview.quality_counts, {
    valid: 256,
    missing: 1,
    invalid_numeric: 1,
    out_of_range: 1,
    off_step: 1,
  });
  await page.getByText('题目与量表', { exact: true }).waitFor();
  await page.getByText('原题 13', { exact: true }).waitFor();
  assert.match(
    await page.getByLabel('评分质量分类').innerText(),
    /有效 256 条.*缺分 1 条.*非法数值 1 条.*超出量表 1 条.*不符合步长 1 条/,
  );
  await page.screenshot({ path: path.join(artifacts, '20x13-preview.png'), fullPage: true });
  await page.getByRole('checkbox', { name: '我已查看评分提醒，确认继续导入' }).check();
  await page.getByRole('button', { name: '下一步', exact: true }).click();
  const committed = page.waitForResponse((r) => r.url().endsWith('/commit') && r.status() === 201);
  await page.getByRole('button', { name: '确认导入', exact: true }).click();
  const receipt = await (await committed).json();
  const table = page.getByRole('table', { name: '逐题统计' });
  await table.waitFor();
  assert.equal(await table.locator('tbody tr[data-row-key]').count(), 13);
  const stats = await page.evaluate(
    async ({ batch_id, snapshot_id }) =>
      (
        await fetch('/api/edu/batches/' + batch_id + '/statistics?snapshot_id=' + snapshot_id)
      ).json(),
    receipt,
  );
  assert.equal(stats.item_statistics.length, 13);
  assert.equal(
    stats.item_statistics.reduce((sum, item) => sum + item.valid_count, 0),
    256,
  );
  assert.equal(
    stats.item_statistics.reduce((sum, item) => sum + item.missing_count, 0),
    1,
  );
  assert.equal(
    stats.item_statistics.reduce((sum, item) => sum + item.invalid_count, 0),
    3,
  );
  assert.equal(
    stats.item_statistics.reduce((sum, item) => sum + item.denominator, 0),
    260,
  );
  for (const item of stats.item_statistics) {
    const row = table.locator('tr[data-row-key="' + item.item_id + '"]');
    assert.match(await row.innerText(), new RegExp(item.mean.display.replace('.', '\\.')));
    assert.equal(
      item.distribution.reduce((sum, bucket) => sum + bucket.count, 0),
      item.valid_count,
    );
    assert.ok(item.distribution.every((bucket) => bucket.denominator === 20));
  }
  await page.screenshot({ path: path.join(artifacts, '20x13-statistics.png'), fullPage: true });
  const first = table.locator('tr[data-row-key="Q01"]');
  await first.getByRole('button', { name: '分布', exact: true }).click();
  await page.getByRole('table', { name: '得分分布' }).waitFor();
  await first.getByRole('button', { name: '评分依据', exact: true }).click();
  const drawer = page.locator('.ant-drawer-open');
  await drawer.getByText('Sheet2!B2', { exact: true }).waitFor();
  await drawer.getByText('S0000', { exact: true }).waitFor();
  await page.waitForFunction(() => {
    const drawer = document.querySelector('.ant-drawer-content-wrapper');
    return (
      drawer &&
      drawer
        .getAnimations({ subtree: true })
        .every((animation) => animation.playState === 'finished')
    );
  });
  await page.screenshot({ path: path.join(artifacts, '20x13-source.png'), fullPage: true });
  await drawer.locator('.ant-drawer-close').click();
  checks.push(
    '20 participants / 13 original questions / 260 observations / 256 valid, 1 missing, 3 invalid / original cell source',
  );
  await page.reload();
  await table.waitFor();
  assert.equal(await table.locator('tbody tr[data-row-key]').count(), 13);
  await page.getByRole('link', { name: '画像探索' }).click();
  const filtered = page.waitForResponse(
    (r) => r.url().includes('item_id=Q13') && r.status() === 200,
  );
  await page.getByRole('combobox', { name: '选择题目' }).fill('原题 13');
  await page.getByText('原题 13', { exact: true }).click();
  assert.equal((await (await filtered).json()).item_statistics[0].item_id, 'Q13');
  await page.getByRole('heading', { name: '原题 13', exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(artifacts, '20x13-mobile.png'), fullPage: true });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('link', { name: '分析报告' }).click();
  await page.getByText('当前测评暂不支持生成或导出分析报告。').waitFor();
  assert.equal(await page.getByRole('button', { name: /导出|下载/ }).count(), 0);
  const legacy = await importThroughPage('empty', '五维兼容小样');
  assert.ok(legacy);
  await page.getByText('评分画像', { exact: true }).waitFor();
  await page.getByRole('combobox', { name: '当前正式测评批次' }).click();
  await page.locator('.ant-select-dropdown').getByText('逐题测评', { exact: true }).click();
  await table.waitFor();
  const stale = await page.evaluate(
    async ({ batch_id }) =>
      (await fetch('/api/edu/batches/' + batch_id + '/statistics?snapshot_id=stale')).status,
    receipt,
  );
  assert.equal(stale, 409);
  checks.push(
    'reload / filtered item_id / 390px / unsupported report / legacy five-dimension upload / batch switch / stale snapshot 409',
  );
  await writeFile(
    path.join(artifacts, 'acceptance.json'),
    JSON.stringify(
      {
        frontend_revision: env.EDU_TEST_FRONTEND_REVISION ?? null,
        backend_revision: env.EDU_TEST_BACKEND_REVISION ?? null,
        status: 'complete',
        checks,
        quality: preview.quality_counts,
        receipt,
        statistics: stats.item_statistics,
      },
      null,
      2,
    ),
  );
  return checks;
}
