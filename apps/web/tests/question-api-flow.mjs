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
  token,
  readerToken,
  importThroughPage,
}) {
  const file = path.join(temporary, '逐题测评.xlsx');
  const unsupported = path.join(temporary, 'unsupported.xlsx');
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
        'other=Workbook(); other.active.append(["任意内容", "不属于测评格式"]); other.save(sys.argv[2])',
      ].join('\n'),
      file,
      unsupported,
    ],
    { env, windowsHide: true },
  );
  const checks = [];
  async function connect(credential) {
    await page.getByRole('button', { name: '登录', exact: true }).first().click();
    const dialog = page.getByRole('dialog', { name: '教师登录', exact: true });
    await dialog.getByRole('button', { name: '使用访问凭据', exact: true }).click();
    await dialog.getByLabel('访问凭据').fill(credential);
    await dialog.getByRole('button', { name: '登录', exact: true }).click();
    await page.locator('.live-workspace').waitFor();
    await dialog.waitFor({ state: 'hidden' });
  }
  const getJson = (url) =>
    page.evaluate(
      async ({ url, token }) => {
        const r = await fetch(url, { headers: { Authorization: 'Bearer ' + token } });
        return { status: r.status, data: await r.json() };
      },
      { url, token },
    );
  await page.goto(origin + '/insights');
  await page.getByRole('heading', { name: '连接账户，查看测评', exact: true }).waitFor();
  await connect(readerToken);
  await page.getByText('当前教师没有导入权，可查看已授权批次。', { exact: true }).waitFor();
  assert.ok(await page.locator('.workflow-dropzone .ant-btn').isDisabled());
  await page.getByRole('button', { name: '结束会话', exact: true }).click();
  await page.goto(origin + '/login');
  await page.getByRole('heading', { name: '教师登录', exact: true }).waitFor();
  await page.getByRole('button', { name: '使用访问凭据', exact: true }).click();
  await page.getByLabel('访问凭据').fill(token);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/insights');
  const heading = page.getByRole('heading', { name: '上传测评资料', exact: true, level: 1 });
  await heading.waitFor();
  const list = await getJson('/api/edu/batches');
  assert.equal(list.status, 200);
  assert.deepEqual(list.data.items, []);
  assert.ok(await page.locator('.workflow-dropzone .ant-btn').isEnabled());
  assert.equal(await page.locator('.radar-panel, .ai-investigation-panel').count(), 0);
  assert.doesNotMatch(
    await page.locator('main').innerText(),
    /Demo|联调测试|正式版|HTTP Mock|AI 分析演示/,
  );
  await page.screenshot({ path: path.join(artifacts, 'first-upload-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(artifacts, 'first-upload-mobile.png'), fullPage: true });
  await page.getByRole('link', { name: '分析报告' }).click();
  await page.getByRole('button', { name: '上传测评资料', exact: true }).click();
  await heading.waitFor();
  await page.setViewportSize({ width: 1440, height: 1000 });
  const rejected = page.waitForResponse(
    (r) => r.url().endsWith('/imports/preview') && r.status() === 422,
  );
  await page.locator('input[type=file]').setInputFiles(unsupported);
  assert.equal((await (await rejected).json()).error.code, 'IMPORT_FORMAT_UNSUPPORTED');
  await page.getByText(/这份表格的结构暂不支持/).waitFor();
  assert.deepEqual((await getJson('/api/edu/batches')).data.items, []);
  await page.getByRole('button', { name: '更换文件', exact: true }).click();
  checks.push(
    'credential login in modal and dedicated page / real empty list / read-only upload gate / unsupported XLSX 422 / 1440px and 390px',
  );
  async function verifyLegacy() {
    const legacy = await importThroughPage('empty', '五维兼容小样');
    assert.ok(legacy);
    await page.getByText('评分画像', { exact: true }).waitFor();
    await page.getByRole('button', { name: '查看历史测评', exact: true }).click();
    await page.getByRole('heading', { name: '历史测评', exact: true }).waitFor();
    await page
      .getByRole('row', { name: /五维兼容小样/ })
      .getByRole('button', { name: '查看学情', exact: true })
      .click();
    await page.getByText('评分画像', { exact: true }).waitFor();
    await page.screenshot({
      path: path.join(artifacts, 'five-dimension-diagnosis.png'),
      fullPage: true,
    });
  }
  if (env.EDU_FIRST_UPLOAD_ONLY === '1') {
    await verifyLegacy();
    checks.push('first five-dimension import opens diagnosis and can be reopened from history');
    return checks;
  }
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
  const statsResponse = await getJson(
    `/api/edu/batches/${receipt.batch_id}/statistics?snapshot_id=${receipt.snapshot_id}`,
  );
  assert.equal(statsResponse.status, 200);
  const stats = statsResponse.data;
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
  await connect(token);
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
  await page.screenshot({
    path: path.join(artifacts, '20x13-reports-desktop.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(artifacts, '20x13-reports-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: '返回学情诊断', exact: true }).click();
  await table.waitFor();
  assert.equal(await table.locator('tbody tr[data-row-key]').count(), 13);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await verifyLegacy();
  await page.getByRole('combobox', { name: '当前测评批次' }).click();
  await page.locator('.ant-select-dropdown').getByText('逐题测评', { exact: true }).click();
  await table.waitFor();
  const stale = (await getJson(`/api/edu/batches/${receipt.batch_id}/statistics?snapshot_id=stale`))
    .status;
  assert.equal(stale, 409);
  checks.push(
    'reload / filtered item_id / 390px / unsupported report returns to diagnosis / legacy five-dimension upload / batch switch / stale snapshot 409',
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
