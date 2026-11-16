// Runs against a separately launched, disposable -Identity first session.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';
import { verifyReportArtifact } from './report-artifact-utils.mjs';

const origin = process.env.EDU_OWNER_ORIGIN;
assert.equal(new URL(origin).hostname, '127.0.0.1');
assert.ok(process.env.EDU_OWNER_SAMPLE_FILE);
assert.ok(process.env.EDU_TEST_PYTHON);
const artifacts = 'artifacts/first-use-owner';
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.EDU_TEST_BROWSER,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(30000);
const errors = [];
const apiFailures = [];
const pendingFailures = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('response', (r) => {
  if (r.url().includes('/api/edu/') && r.status() >= 400) {
    pendingFailures.push(
      r
        .json()
        .catch(() => ({}))
        .then((body) => {
          apiFailures.push({ status: r.status(), code: body.error?.code ?? null });
        }),
    );
  }
});
page.on('request', (request) => {
  assert.ok(!/qwen|dashscope/i.test(request.url()));
  if (request.url().includes('/api/edu/')) assert.equal(request.headers().authorization, undefined);
});
const response = (suffix, status) =>
  page.waitForResponse((r) => r.url().endsWith(suffix) && r.status() === status);
try {
  await page.goto(origin + '/insights');
  await page.locator('input[type=file]').waitFor({ state: 'attached' });
  const previewed = response('/imports/preview', 201);
  await page.locator('input[type=file]').setInputFiles(process.env.EDU_OWNER_SAMPLE_FILE);
  assert.equal((await (await previewed).json()).summary.error_count, 0);
  await page.getByRole('heading', { name: '文件检查结果', exact: true }).waitFor();
  const ack = page.getByRole('checkbox', { name: /我已查看以上提醒/ });
  if (await ack.count()) await ack.check();
  await page.getByRole('button', { name: '下一步：确认导入', exact: true }).click();
  const committed = response('/commit', 201);
  await page.getByRole('button', { name: '确认导入', exact: true }).click();
  const batch = await (await committed).json();
  await page.locator('.discoveries-panel').waitFor();
  const created = response('/ai-investigation-tasks', 202);
  await page.getByRole('button', { name: '优先核查', exact: true }).click();
  await page.getByRole('button', { name: '开始分析', exact: true }).click();
  const task = await (await created).json();
  assert.equal(task.execution_origin, 'http_mock');
  const cards = page.getByTestId('ai-fact-card');
  await cards.first().waitFor({ timeout: 240000 });
  await cards.first().getByRole('button', { name: '查看评分依据', exact: true }).click();
  const drawer = page.getByRole('dialog', { name: '评分依据', exact: true });
  await drawer.getByRole('button', { name: '查看原评分依据', exact: true }).first().click();
  await page.getByText('原始数值：', { exact: false }).waitFor();
  await page.locator('.ant-drawer-open').last().locator('.ant-drawer-close').click();
  await drawer.locator('.ant-drawer-close').click();
  await cards.getByRole('button', { name: '继续探索', exact: true }).first().click();
  await page.getByText('应用筛选', { exact: true }).waitFor();
  await page.getByRole('link', { name: '学情诊断', exact: true }).click();
  await cards.first().waitFor();
  await page.reload();
  await cards.first().waitFor();
  assert.equal(await page.getByRole('button', { name: '开始分析', exact: true }).count(), 0);
  await page.screenshot({ path: path.join(artifacts, 'uploaded-ai.png') });
  const submitted = response('/review-submissions', 200);
  await page.getByRole('button', { name: '前往报告核查', exact: true }).click();
  const receipt = await (await submitted).json();
  const review = page.getByRole('region', { name: '分析核验', exact: true });
  await review.locator('.review-finding').first().waitFor();
  for (const finding of await review.locator('.review-finding').all()) {
    if ((await finding.getAttribute('open')) === null)
      await finding.locator(':scope > summary').click();
    await finding
      .getByRole('radio', { name: '已确认', exact: true })
      .locator('xpath=ancestor::label[1]')
      .click();
  }
  const saved = page.waitForResponse(
    (r) => r.url().endsWith('/' + receipt.result_id) && r.request().method() === 'PATCH',
  );
  await review.getByRole('button', { name: '保存核验结果', exact: true }).click();
  const savedResponse = await saved;
  assert.equal(savedResponse.status(), 200);
  const result = await savedResponse.json();
  await review.getByText('已保存版本 ' + result.version, { exact: true }).waitFor();
  const downloads = [];
  for (const format of ['md', 'xlsx']) {
    const checked = page.waitForResponse(
      (r) => r.url().endsWith('/export-check') && r.request().postDataJSON().format === format,
    );
    await review.getByRole('button', { name: '导出测评报告', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '导出测评报告', exact: true });
    if (format === 'md') await dialog.getByText('Markdown', { exact: true }).click();
    assert.equal((await checked).status(), 200);
    const accepted = dialog.getByRole('checkbox');
    if (await accepted.count()) await accepted.check();
    const [file] = await Promise.all([
      page.waitForEvent('download'),
      dialog
        .getByRole('button', {
          name: format === 'md' ? '下载 Markdown' : '下载 Excel',
          exact: true,
        })
        .click(),
    ]);
    const bytes = await readFile(await file.path());
    const destination = path.join(artifacts, 'uploaded-confirmed.' + format);
    await writeFile(destination, bytes);
    downloads.push(
      verifyReportArtifact({
        format,
        bytes,
        destination,
        python: process.env.EDU_TEST_PYTHON,
        env: process.env,
        reference: {
          savedResult: result,
          studentCount: 53,
          batchId: batch.batch_id,
          snapshotId: batch.snapshot_id,
          resultId: result.result_id,
          version: result.version,
          discoveryIds: result.discovery_findings
            .filter((f) => f.teacher_review.status === 'confirmed')
            .map((f) => f.discovery.discovery_id),
        },
      }),
    );
    await dialog.locator('.ant-modal-close').click();
  }
  await page.screenshot({ path: path.join(artifacts, 'uploaded-confirmed.png') });
  await Promise.all(pendingFailures);
  assert.deepEqual(errors, []);
  assert.deepEqual(apiFailures, [], 'normal first-use flow must not hide failed API responses');
  const record = {
    passed: true,
    execution_origin: 'http_mock',
    real_qwen: 'not_run',
    scenario: 'registered first-use XLSX upload through saved AI report in one batch',
    page_errors: errors,
    api_failures: apiFailures,
    downloads,
  };
  await writeFile(path.join(artifacts, 'acceptance.json'), JSON.stringify(record, null, 2));
  console.log(JSON.stringify(record));
} catch (error) {
  await page.screenshot({ path: path.join(artifacts, 'failure.png') });
  await Promise.all(pendingFailures);
  await writeFile(
    path.join(artifacts, 'failure.json'),
    JSON.stringify(
      {
        message: error.message,
        api_failures: apiFailures,
        page_errors: errors,
      },
      null,
      2,
    ),
  );
  throw error;
} finally {
  await browser.close();
}
