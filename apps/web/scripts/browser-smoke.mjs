import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';

async function main() {
  const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  const browser = await chromium.launch({
    headless: true,
    ...(existsSync(chrome) ? { executablePath: chrome } : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
  });
  const errors = [],
    warnings = [],
    checks = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (['warning', 'error'].includes(m.type())) warnings.push(m.text());
  });
  const artifacts = path.resolve('artifacts');
  await mkdir(artifacts, { recursive: true });
  const shot = async (name) => {
    await page.waitForTimeout(400);
    await page.screenshot({
      path: path.join(artifacts, name + '.png'),
      fullPage: (await page.locator('.ant-drawer-open').count()) === 0,
    });
  };
  const noOverflow = async () =>
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
      false,
      '页面不能整体横向溢出',
    );
  const uploadContained = async () => {
    const uploader = await page.locator('.excel-dropzone').boundingBox();
    const panel = await page.locator('.upload-panel').boundingBox();
    assert.ok(
      uploader.y + uploader.height < panel.y + panel.height,
      '上传区应完整包含在上传卡片中',
    );
    const preparation = await page.locator('.preparation-panel').boundingBox();
    if (preparation && preparation.y > panel.y + 50) {
      assert.ok(panel.y + panel.height <= preparation.y, '窄屏上传卡片不能遮挡数据准备卡片');
    }
  };
  const closeDrawer = () => page.locator('.ant-drawer-open .ant-drawer-close').click();
  try {
    const response = await page.goto(process.env.APP_URL || 'http://127.0.0.1:3000/');
    assert.equal(response.status(), 200);
    await page.goto(new URL('/dev/demo', process.env.APP_URL || 'http://127.0.0.1:3000/').href);
    await page.getByRole('heading', { name: '本次测评概况' }).waitFor();
    assert.match(await page.locator('.stat-grid').innerText(), /参评人数/);
    assert.doesNotMatch(
      await page.locator('.workspace').innerText(),
      /班级|全班|课堂成绩|算法比赛/,
    );
    await page.locator('.ability-chart svg').waitFor();
    const demoLabels = await page
      .locator('.finding-top .ant-tag')
      .evaluateAll((elements) =>
        elements.map((element) => parseFloat(getComputedStyle(element).fontSize)),
      );
    assert.ok(demoLabels.length > 0 && demoLabels.every((size) => size <= 13));
    await noOverflow();
    await shot('insights-desktop');
    checks.push('桌面首页及雷达图正常渲染');
    await page.getByRole('button', { name: /查看 21 人依据/ }).click();
    await page.getByText('统计分母：本次测评参评人数 105 人').waitFor();
    await shot('evidence-desktop');
    await closeDrawer();
    await page.getByRole('button', { name: '生成测评分析' }).click();
    await page.getByText('规则模板 · 未接入 AI').waitFor();
    await closeDrawer();
    checks.push('证据抽屉与明确标注的分析模板可查看');
    await page.getByRole('link', { name: '画像探索', exact: true }).click();
    await page.locator('.match-summary').filter({ hasText: '21' }).waitFor();
    await page.getByRole('button', { name: '查看 S008 详情', exact: true }).click();
    await page.getByText('匿名学生 S008', { exact: true }).waitFor();
    await shot('student-desktop');
    await page.getByRole('button', { name: '下一名学生' }).click();
    await page.getByRole('button', { name: '上一名学生' }).click();
    await page.getByText('匿名学生 S008', { exact: true }).waitFor();
    await closeDrawer();
    await page.getByRole('button', { name: /全屏$/ }).click();
    await page.waitForFunction(() => Boolean(document.fullscreenElement));
    await page.locator('.explorer-fullscreen-grid').waitFor();
    assert.match(await page.locator('.match-summary').innerText(), /21/);
    await shot('explorer-fullscreen');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.fullscreenElement);
    const scoreInput = page.getByRole('spinbutton', { name: '条件1评分' });
    await scoreInput.fill('100');
    await page.getByRole('button', { name: '应用筛选', exact: true }).click();
    await page.getByText('没有符合当前条件的学生，请调整或重置筛选。').waitFor();
    assert.equal(await page.getByRole('button', { name: /分析这组学生$/ }).isDisabled(), true);
    await page.getByRole('button', { name: '重置', exact: true }).click();
    assert.match(await page.locator('.match-summary').innerText(), /105/);
    await shot('explorer-desktop');
    checks.push('筛选、空结果、重置和学生前后切换通过');
    await page.getByRole('button', { name: '上传新测评', exact: true }).click();
    await page.getByRole('heading', { name: '上传新测评' }).waitFor();
    await noOverflow();
    await uploadContained();
    await shot('data-desktop');
    await page.getByRole('button', { name: /全屏$/ }).click();
    await page.waitForFunction(() => Boolean(document.fullscreenElement));
    await page.getByRole('heading', { name: '当前批次数据概览' }).waitFor();
    await shot('data-fullscreen');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.fullscreenElement);
    await page
      .locator('input[type=file]')
      .setInputFiles(path.resolve('public/templates/评价_待校验示例.xlsx'));
    await page.getByLabel('测评名称', { exact: true }).waitFor();
    assert.equal(
      await page.getByLabel('测评名称', { exact: true }).inputValue(),
      '评价_待校验示例',
    );
    assert.equal(await page.getByRole('heading', { name: '导入预览', exact: true }).count(), 0);
    await page.getByRole('button', { name: '下一步：预览校验', exact: true }).click();
    await page.getByRole('heading', { name: '导入预览', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: /确认导入$/ }).isDisabled(), true);
    assert.match(await page.locator('.quality-report').innerText(), /2 项需处理/);
    await page.getByRole('button', { name: '定位记录 →', exact: true }).first().click();
    await page.locator('.located-row').waitFor();
    await shot('import-errors-desktop');
    await page.getByRole('button', { name: /返回上传$/ }).click();
    checks.push('真实 Excel 问题文件被阻断且可定位原始行');
    await page
      .locator('input[type=file]')
      .setInputFiles(path.resolve('public/templates/评价长表模板.xlsx'));
    await page.getByLabel('测评名称', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('测评名称', { exact: true }).inputValue(), '评价长表模板');
    await page.getByLabel('测评名称', { exact: true }).fill('浏览器验证批次');
    await page.getByRole('button', { name: '下一步：预览校验', exact: true }).click();
    await page.getByText('所有记录通过当前标准校验').waitFor();
    assert.equal(await page.getByRole('button', { name: /确认导入$/ }).isEnabled(), true);
    assert.equal(await page.getByLabel('测评名称', { exact: true }).inputValue(), '浏览器验证批次');
    await page.getByRole('button', { name: /确认导入$/ }).click();
    await page.getByText('浏览器验证批次', { exact: true }).first().waitFor();
    assert.equal(await page.locator('.batches-panel tbody tr.ant-table-row').count(), 2);
    const sameFile = await readFile(path.resolve('public/templates/评价长表模板.xlsx'));
    await page.locator('input[type=file]').setInputFiles({
      name: '重命名.xlsx',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      buffer: sameFile,
    });
    await page.getByLabel('测评名称', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('测评名称', { exact: true }).inputValue(), '重命名');
    await page.getByRole('button', { name: '下一步：预览校验', exact: true }).click();
    await page.getByText('这份文件已在当前会话中导入，重命名文件也不会创建重复批次。').waitFor();
    checks.push('有效文件确认追加批次，重命名重复文件仍被识别');
    await page.getByRole('link', { name: '学情诊断', exact: true }).click();
    assert.match(await page.locator('.stat-grid').innerText(), /2/);
    await page.getByRole('button', { name: /全屏$/ }).click();
    await page.waitForFunction(() => Boolean(document.fullscreenElement));
    assert.equal(await page.getByRole('navigation', { name: '主导航' }).count(), 0);
    await shot('insights-fullscreen');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.fullscreenElement);
    await page.getByRole('navigation', { name: '主导航' }).waitFor();
    checks.push('全屏隐藏主导航，Esc 退出并保留批次');
    await page.reload();
    await page.goto(new URL('/dev/demo', process.env.APP_URL || 'http://127.0.0.1:3000/').href);
    await page.getByRole('heading', { name: '本次测评概况' }).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await noOverflow();
    await shot('insights-mobile');
    await page.getByRole('link', { name: '画像探索', exact: true }).click();
    await page.getByRole('heading', { name: '能力组合分布' }).waitFor();
    await noOverflow();
    await shot('explorer-mobile');
    await page.getByRole('button', { name: '上传新测评', exact: true }).click();
    await page.getByRole('heading', { name: '上传新测评' }).waitFor();
    await noOverflow();
    await uploadContained();
    await shot('data-mobile');
    checks.push('390px 窄屏三页无整体横向溢出');
    assert.deepEqual(errors, [], '无页面运行时异常');
    const result = { checks, errors, warnings };
    await writeFile(path.join(artifacts, 'browser-smoke.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (e) {
    await shot('failure');
    throw e;
  } finally {
    await browser.close();
  }
}
main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
