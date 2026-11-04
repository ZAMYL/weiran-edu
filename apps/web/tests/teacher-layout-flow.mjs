import assert from 'node:assert/strict';
import path from 'node:path';
import { writeFile } from 'node:fs/promises';
import { portraitPaginationFlow } from './portrait-pagination-flow.mjs';
const sizes = [
  [1440, 900],
  [1920, 1080],
  [1024, 768],
  [960, 900],
  [390, 844],
];
const uploadFrames = new Map();
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
    if (
      name === 'data' ||
      name === 'upload-selected' ||
      name === 'upload-confirm' ||
      name.startsWith('preview-')
    ) {
      assert.equal(await page.locator('.upload-workflow').count(), 1);
      assert.equal(await page.locator('.upload-workflow .ant-steps').count(), 1);
      assert.equal(await page.locator('.assessment-upload-details').count(), 0);
      const workflow = await page.locator('.upload-workflow').boundingBox();
      assert.ok(workflow.width <= 1320 && workflow.width <= width);
      if (width > 1384) assert.ok(Math.abs(workflow.width - 1320) <= 1);
      const frameKey = `${width}x${height}`;
      const first = uploadFrames.get(frameKey);
      if (!first) uploadFrames.set(frameKey, workflow);
      else {
        assert.ok(Math.abs(workflow.width - first.width) <= 1);
        assert.ok(
          Math.abs(workflow.height - first.height) <= 1,
          'all upload stages have identical frame height',
        );
      }
      const steps = page.locator('.upload-workflow .ant-steps-item');
      const step1 = await steps.first().boundingBox();
      const step3 = await steps.last().boundingBox();
      assert.ok(step3.y > step1.y + step1.height, 'steps stay in a left vertical column');
      assert.ok(Math.abs(step3.x - step1.x) <= 1);
      if (name === 'data') {
        const body = await page.locator('.upload-workflow-body').boundingBox();
        const drop = await page.locator('.workflow-dropzone .ant-upload-drag').boundingBox();
        assert.ok(
          drop.width > body.width * 0.8 && drop.height > body.height * 0.8,
          'upload target fills the available right pane',
        );
        assert.doesNotMatch(
          await page.locator('.upload-workflow-body').innerText(),
          /选择后自动检查|确认前不会入库/,
        );
      }
    }
    if (name === 'diagnosis' || name === 'statistics') {
      const regions = await page.evaluate(() => {
        const box = (selector) => {
          const rect = document.querySelector(selector).getBoundingClientRect();
          return {
            x: rect.x,
            right: rect.right,
            y: rect.y,
            bottom: rect.bottom,
            width: rect.width,
          };
        };
        return {
          intro: box('.workspace-intro'),
          title: box('.page-title-group'),
          toolbar: box('.assessment-toolbar'),
          summary: box('.workspace-summary'),
          radar: box('.radar-panel'),
          analysis: box('.insights-right'),
        };
      });
      const { intro, title, toolbar, summary, radar, analysis } = regions;
      assert.ok(
        title.y >= intro.y && toolbar.y >= title.bottom && toolbar.bottom <= intro.bottom + 1,
      );
      assert.ok(toolbar.x >= intro.x && toolbar.right <= intro.right + 1);
      if (width > 960) {
        assert.ok(
          Math.abs(intro.x - radar.x) <= 1 &&
            Math.abs(intro.width - radar.width) <= 1 &&
            Math.abs(summary.x - analysis.x) <= 1 &&
            Math.abs(summary.width - analysis.width) <= 1 &&
            Math.abs(intro.y - summary.y) <= 1,
          `${name}: header regions align with their cards at ${width}px: ${JSON.stringify(regions)}`,
        );
      } else {
        assert.ok(
          summary.y >= intro.bottom && Math.abs(summary.width - intro.width) <= 1,
          `${name}: title and assessment actions stay together before overview at ${width}px`,
        );
        assert.ok(
          analysis.bottom <= radar.y,
          `${name}: narrow screens show actionable findings before the overview chart`,
        );
      }
    }
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
  assert.equal(await page.getByRole('button', { name: '上传新测评', exact: true }).count(), 0);
  assert.equal(await page.getByRole('combobox', { name: '当前测评批次' }).count(), 0);
  assert.equal(await page.getByText('更多信息（可选）', { exact: true }).count(), 0);
  await clean();
  await capture(page, artifacts, 'data');
  const imported = await importThroughPage('contrast105', '合成竞赛测评 · 105人');
  assert.equal(imported.data.scope.batch_record_count, 105);
  await ready();
  assert.equal(await page.locator('.stat-grid strong').first().innerText(), '105');
  assert.equal(await focus().getByTestId('discovery-card').count(), 1);
  assert.equal(await page.locator('input[type=file]').count(), 0);
  await clean();
  await capture(page, artifacts, 'diagnosis');
  await page.screenshot({ path: path.join(artifacts, 'after-diagnosis-full.png'), fullPage: true });
  const layout = await page.evaluate(() => {
    const discover = document.querySelector('.discoveries-panel').getBoundingClientRect();
    const radar = document.querySelector('.radar-panel').getBoundingClientRect();
    const charts = document.querySelector('.insights-grid').getBoundingClientRect();
    const all = document.querySelector('.all-discoveries-panel').getBoundingClientRect();
    const right = document.querySelector('.discoveries-panel');
    return {
      discoveryTop: discover.top,
      radarTop: radar.top,
      discoveryLeft: discover.left,
      radarRight: radar.right,
      allTop: all.top,
      chartsBottom: charts.bottom,
      rightScroll:
        ['scroll', 'auto'].includes(getComputedStyle(right).overflowY) &&
        right.scrollHeight > right.clientHeight + 1,
    };
  });
  assert.ok(
    Math.abs(layout.discoveryTop - layout.radarTop) <= 1 &&
      layout.discoveryLeft > layout.radarRight &&
      layout.allTop >= layout.chartsBottom,
  );
  assert.equal(layout.rightScroll, false);
  const matchedCards = async () => {
    const boxes = await page.evaluate(() => {
      const left = document.querySelector('.radar-panel').getBoundingClientRect();
      const panel = document.querySelector('.insights-right > div:not([hidden]) > .panel');
      const right = panel.getBoundingClientRect();
      const last = panel.lastElementChild.getBoundingClientRect();
      return {
        leftWidth: left.width,
        rightWidth: right.width,
        leftHeight: left.height,
        rightHeight: right.height,
        bottomBlank: right.bottom - last.bottom - parseFloat(getComputedStyle(panel).paddingBottom),
      };
    });
    assert.ok(
      Math.abs(boxes.leftWidth - boxes.rightWidth) <= 1 &&
        Math.abs(boxes.leftHeight - boxes.rightHeight) <= 1 &&
        boxes.rightHeight > 0 &&
        boxes.bottomBlank <= 24,
      JSON.stringify(boxes),
    );
  };
  await matchedCards();
  assert.equal(await page.locator('.topbar .fullscreen-button').count(), 1);
  assert.equal(await page.locator('.assessment-toolbar .fullscreen-button').count(), 0);
  const overview = await page.locator('.assessment-overview').boundingBox();
  const heading = await page.locator('.page-heading').boundingBox();
  assert.ok(
    overview.height < 140 &&
      overview.x > heading.x + heading.width / 2 &&
      overview.y < heading.y + heading.height,
  );

  const pane = (value) => page.getByRole('radio', { name: value, exact: true }).locator('..');
  assert.equal(await page.getByRole('radio', { name: '重点关注', exact: true }).isChecked(), true);
  await focus().getByRole('checkbox').first().check();
  const chosen = await focus().getByTestId('discovery-card').getAttribute('data-discovery-id');
  const picker = page.getByRole('combobox', { name: '查看重点发现', exact: true });
  await picker.click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').nth(1).click();
  assert.notEqual(
    await focus().getByTestId('discovery-card').getAttribute('data-discovery-id'),
    chosen,
  );
  assert.equal(await focus().getByRole('checkbox').isChecked(), false);
  await picker.click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
  assert.equal(await focus().getByRole('checkbox').isChecked(), true);
  await pane('评分统计').click();
  await page.getByRole('combobox', { name: '查看洞察方面', exact: true }).waitFor();
  assert.equal(await focus().isVisible(), false);
  await matchedCards();
  await capture(page, artifacts, 'statistics');
  await pane('重点关注').click();
  assert.equal(await focus().getByRole('checkbox').first().isChecked(), true);
  await focus().getByRole('checkbox').first().uncheck();
  await focus().getByRole('button', { name: '查看发现与依据', exact: true }).first().click();
  await page.getByTestId('discovery-chart').locator('svg').waitFor();
  const detailSelection = page.locator('.discovery-detail-selection').getByRole('checkbox');
  await detailSelection.check();
  assert.equal(await focus().getByRole('checkbox').isChecked(), true);
  await detailSelection.uncheck();
  assert.equal(await focus().getByRole('checkbox').isChecked(), false);
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
  await focus().getByRole('button', { name: '继续探索这组学生', exact: true }).first().click();
  await page.locator('.match-summary').filter({ hasText: '105 / 105' }).waitFor();
  assert.ok(new URL(page.url()).searchParams.has('discovery'));
  const portraitReply = page
    .waitForResponse(
      (response) =>
        /\/students\/[^/]+\/evidence\?/.test(response.url()) && response.status() === 200,
    )
    .then((response) => response.json());
  await page
    .getByRole('button', { name: / 能力画像$/ })
    .first()
    .click();
  const individual = await portraitReply;
  const portrait = () => page.getByTestId('student-portrait-content');
  await portrait().waitFor();
  assert.equal(await portrait().getAttribute('data-student-id'), individual.student_id);
  assert.equal(individual.items.length, 5);
  assert.equal(await portrait().locator('.ant-table-tbody tr').count(), 5);
  await portrait()
    .getByRole('img', { name: /^学生能力雷达图/ })
    .waitFor();
  assert.ok(
    new URL(page.url()).searchParams.has('discovery'),
    'individual view retains original group URL',
  );
  await page.locator('.match-summary').filter({ hasText: '105 / 105' }).waitFor();
  const headingAlignment = await page.evaluate(() => {
    const title = document.querySelector('.page-title-group h1').getBoundingClientRect();
    const mode = document.querySelector('.explorer-view-controls').getBoundingClientRect();
    return Math.abs(title.top + title.height / 2 - (mode.top + mode.height / 2));
  });
  assert.ok(headingAlignment <= 12, `compact header: ${headingAlignment}`);
  const compare = page.getByRole('checkbox', { name: '对照测评平均分', exact: true });
  await compare.uncheck();
  assert.equal(
    await portrait().getByRole('columnheader', { name: '测评平均', exact: true }).count(),
    0,
  );
  await compare.check();
  const secondReply = page
    .waitForResponse(
      (response) =>
        /\/students\/[^/]+\/evidence\?/.test(response.url()) && response.status() === 200,
    )
    .then((response) => response.json());
  await compare.uncheck();
  await page.getByRole('list', { name: '选择学生画像' }).getByRole('button').nth(1).click();
  const secondStudent = await secondReply;
  await page.waitForFunction(
    (id) =>
      document
        .querySelector('[data-testid="student-portrait-content"]')
        ?.getAttribute('data-student-id') === id,
    secondStudent.student_id,
  );
  assert.notEqual(secondStudent.student_id, individual.student_id);
  assert.equal(
    await compare.isChecked(),
    false,
    'comparison choice survives selecting another student',
  );
  await compare.check();
  const scoreReply = page
    .waitForResponse(
      (response) =>
        /\/batches\/[^/]+\/evidence\/[^/]+\?/.test(response.url()) && response.status() === 200,
    )
    .then((response) => response.json());
  await portrait()
    .getByRole('button', { name: /个人评分依据$/ })
    .first()
    .click();
  const individualScore = await scoreReply;
  assert.equal(individualScore.student_id, secondStudent.student_id);
  assert.equal(individualScore.batch_id, imported.batchId);
  await closeTop();
  const missingPattern = `**/students/${individual.student_id}/evidence?**`;
  await page.route(missingPattern, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ response, json: { ...data, items: data.items.slice(1) } });
  });
  await page.getByRole('list', { name: '选择学生画像' }).getByRole('button').first().click();
  await portrait()
    .getByRole('img', { name: '不完整学生评分图；未提供评分的维度不补零', exact: true })
    .waitFor();
  assert.equal(
    await portrait()
      .getByRole('img', { name: /^学生能力雷达图/ })
      .count(),
    0,
  );
  assert.ok((await portrait().innerText()).includes('未提供有效评分'));
  await page.unroute(missingPattern);
  await page.screenshot({
    path: path.join(artifacts, 'student-portrait-incomplete.png'),
    fullPage: true,
  });
  await page.getByRole('list', { name: '选择学生画像' }).getByRole('button').nth(1).click();
  await portrait()
    .getByRole('img', { name: /^学生能力雷达图/ })
    .waitFor();
  await page.screenshot({
    path: path.join(artifacts, 'student-portrait-desktop.png'),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileStudent = page.getByRole('combobox', { name: '选择当前页学生画像', exact: true });
  await mobileStudent.click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
  await page.locator('.ant-select-dropdown:visible').waitFor({ state: 'hidden' });
  await page.waitForFunction(
    (id) =>
      document
        .querySelector('[data-testid="student-portrait-content"]')
        ?.getAttribute('data-student-id') === id,
    individual.student_id,
  );
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({
    path: path.join(artifacts, 'student-portrait-mobile.png'),
    fullPage: true,
  });
  await page.locator('.portrait-member-panel .ant-pagination-next').click();
  await page.waitForFunction(
    () =>
      document.querySelector('.student-portrait-workspace')?.getAttribute('aria-busy') === 'false',
  );
  assert.equal(
    await portrait().getAttribute('data-student-id'),
    individual.student_id,
    'member pagination retains the selected portrait',
  );
  await portraitPaginationFlow(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole('radio', { name: '群体探索', exact: true }).locator('..').click();
  await page.locator('.group-summary-card .group-distribution').waitFor();
  const groupLayout = await page.evaluate(() => {
    const summary = document.querySelector('.group-summary-card');
    const heading = summary.querySelector('.panel-heading').getBoundingClientRect();
    const first = summary.querySelector('.group-distribution').getBoundingClientRect();
    const panels = [...document.querySelectorAll('.explorer-grid > .panel')].map((panel) =>
      panel.getBoundingClientRect(),
    );
    return {
      gap: first.top - heading.bottom,
      heightDifference: panels[0].height - panels[1].height,
    };
  });
  assert.ok(groupLayout.gap <= 32, 'group scores start below their heading without a blank spacer');
  assert.ok(
    Math.abs(groupLayout.heightDifference) <= 1,
    'desktop exploration panels have equal height',
  );
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
  await focus().getByText('当前范围没有符合条件的发现', { exact: true }).waitFor();
  await focus()
    .getByText(/不代表没有学习问题/)
    .waitFor();
  await focus().getByRole('button', { name: '查看评分统计', exact: true }).click();
  assert.equal(await page.getByRole('radio', { name: '评分统计', exact: true }).isChecked(), true);
  await pane('重点关注').click();
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
        uploadFrames: Object.fromEntries(uploadFrames),
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
    'Automatic checks, name edits and preview survive navigation; all three upload frames keep identical sizes with vertical steps; injected post-commit read failure recovers without a second commit',
    'One focused finding at a time; switching finding and statistics preserves report selection',
    'Individual portrait uses full authorized student evidence; comparison, selection, original group, score evidence and missing-score bars verified; desktop/mobile screenshots',
    'Evidence drawer can add/remove a report selection; zero discoveries explain limits and link to statistics; mobile shows findings before chart',
    'No-data home uploads directly; confirmation opens diagnosis; second upload switches to 17-student zero findings',
    '105-student real findings/charts/owned evidence, controlled further exploration, memory-only report draft across navigation; batch change clears draft',
    'Current authorized batch survives reload and reopening even after a newer batch exists; history remains a secondary workspace entry',
    'Actual desktop, 1024/960 breakpoint and mobile screenshots; grouped title/actions and overview align with cards or stack in order without overflow',
  ];
}

teacherLayoutFlow.capture = capture;
