'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  InputNumber,
  Menu,
  Pagination,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import { createPortal } from 'react-dom';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import {
  DIMENSIONS,
  type ApiBatch,
  type ApiCondition,
  type ApiStudent,
  type DimensionCode,
} from '../../services/edu-api/types';
import {
  conditionText,
  dimensionName,
  operatorLabels,
  parseThreshold,
  scoreDisplay,
  ratioDisplay,
} from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, NoBatch, RequestError } from './LiveStatus';
import { SparseMatrix, ServerAbilityChart } from './ServerCharts';
import { ScoreReading } from './TeacherScoreReading';
import { describeDiscovery } from '../../services/edu-api/teacher-analysis';
import { LiveScoreDrawer, LiveStudentDrawer, LiveStudentTable } from './LiveStudents';
import AnalysisActions from './AnalysisActions';
import { DiscoveryChart, scoreText } from './DiscoveryChart';
import StudentPortrait from './StudentPortrait';
import AssessmentItemPage from './AssessmentItemPage';

interface Draft {
  dimension_code: DimensionCode;
  operator: ApiCondition['operator'];
  value: string;
}
export default function LiveExplorerPage() {
  const { batch } = useDataMode();
  if (batch?.recognized_items)
    return <AssessmentItemPage key={batch.snapshot_id} batch={batch} view="explorer" />;
  return batch ? (
    <Explorer key={`${batch.batch_id}:${batch.snapshot_id}`} batch={batch} />
  ) : (
    <NoBatch />
  );
}
function Explorer({ batch }: { batch: ApiBatch }) {
  const { client, conditions, setConditions, discoveryOrigin } = useDataMode();
  const { summaryHost } = useWorkspaceLayout();
  const [view, setView] = useState<'group' | 'student'>('group');
  const [portraitStudent, setPortraitStudent] = useState<ApiStudent | null>(null);
  const portraitWorkspace = useRef<HTMLDivElement>(null);
  const queryPanel = useRef<HTMLDivElement>(null);
  const [pageSpace, setPageSpace] = useState<{
    workspaceHeight: number;
    queryHeight: number;
    matched: number;
  } | null>(null);
  const origin =
    discoveryOrigin?.batch_id === batch.batch_id &&
    discoveryOrigin.spec.snapshot_id === batch.snapshot_id
      ? discoveryOrigin
      : null;
  const [draft, setDraft] = useState<Draft[]>(() =>
    conditions.map((c) => ({ ...c, value: String(c.value) })),
  );
  const [previousConditions, setPreviousConditions] = useState(conditions);
  if (previousConditions !== conditions) {
    setPreviousConditions(conditions);
    setDraft(conditions.map((condition) => ({ ...condition, value: String(condition.value) })));
  }
  const [validation, setValidation] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [axes, setAxes] = useState<{ x: DimensionCode; y: DimensionCode }>({
    x: 'algorithm_data_structure',
    y: 'solution_strategy',
  });
  const [student, setStudent] = useState<ApiStudent | null>(null);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const loadStats = useCallback(
    (signal: AbortSignal) => client.statistics(batch, axes.x, axes.y, signal),
    [client, batch, axes],
  );
  const stats = useRemote(
    `explorer-stats:${batch.batch_id}:${batch.snapshot_id}:${axes.x}:${axes.y}`,
    loadStats,
  );
  const loadGroup = useCallback(
    async (signal: AbortSignal) => {
      if (origin) {
        const detail = await client.discoveryDetail(
          batch,
          origin.discovery_id,
          origin.spec,
          signal,
        );
        const records = await client.discoveryRecords(
          batch,
          origin.discovery_id,
          origin.metric_key,
          origin.spec,
          offset,
          signal,
        );
        return {
          items: records.items,
          matched: records.total_count,
          total: records.scope.scope_record_count,
          ratio: null,
          statistics: null,
          detail,
        };
      }
      if (conditions.length) {
        const group = await client.group(batch, conditions, offset, signal);
        return {
          items: group.items,
          matched: group.matched_count,
          total: group.total_count,
          ratio: group.ratio,
          statistics: group.statistics,
          detail: null,
        };
      }
      const page = await client.students(batch, offset, signal);
      return {
        items: page.items,
        matched: page.total_count,
        total: page.total_count,
        ratio: page.total_count ? 1 : 0,
        statistics: null,
        detail: null,
      };
    },
    [client, batch, conditions, offset, origin],
  );
  const group = useRemote(
    `group:${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}:${JSON.stringify(origin)}:${offset}`,
    loadGroup,
  );
  const loadRubric = useCallback(
    (signal: AbortSignal) => client.rubric(batch, signal),
    [client, batch],
  );
  const rubric = useRemote(
    !origin && conditions.length ? `explorer-rubric:${batch.batch_id}:${batch.snapshot_id}` : null,
    loadRubric,
  );
  // Validate the typed text before any numeric normalization can change the threshold.
  const updateThreshold = (index: number, value: string) =>
    setDraft((current) =>
      current.map((condition, i) => (i === index ? { ...condition, value } : condition)),
    );
  function apply() {
    const next: ApiCondition[] = [];
    for (const condition of draft) {
      const value = parseThreshold(condition.value);
      if (value === null) {
        setValidation('评分阈值须为 1–5，最多两位有效小数；原输入不会自动舍入。');
        return;
      }
      next.push({ ...condition, value });
    }
    if (new Set(next.map((c) => c.dimension_code)).size !== next.length) {
      setValidation('两个条件必须使用不同维度。');
      return;
    }
    setValidation(null);
    setPageSpace(null);
    setOffset(0);
    setStudent(null);
    setPortraitStudent(null);
    setEvidenceId(null);
    setConditions(next);
  }
  const selectCell = useCallback(
    (x: number, y: number) => {
      const next: ApiCondition[] = [
        { dimension_code: axes.x, operator: 'eq', value: x },
        { dimension_code: axes.y, operator: 'eq', value: y },
      ];
      setDraft(next.map((c) => ({ ...c, value: String(c.value) })));
      setConditions(next);
      setPageSpace(null);
      setOffset(0);
      setValidation(null);
      setStudent(null);
      setPortraitStudent(null);
      setEvidenceId(null);
    },
    [axes, setConditions],
  );
  const distribution = group.data?.statistics ?? (conditions.length || origin ? null : stats.data);
  const originalConditions = group.data?.detail?.conditions
    .map(
      (c) =>
        `${dimensionName(c.dimension_code)} ${operatorLabels[c.operator]} ${scoreText(c.threshold)}`,
    )
    .join(' 且 ');
  const targetStats =
    group.data?.detail?.type === 'group_difference' ? group.data.detail.chart : null;
  const unapplied =
    draft.length !== conditions.length ||
    draft.some(
      (condition, index) =>
        condition.dimension_code !== conditions[index]?.dimension_code ||
        condition.operator !== conditions[index]?.operator ||
        parseThreshold(condition.value) !== conditions[index]?.value,
    );
  const viewControls = (
    <Flex align="center" gap={tokens.sizeSM} wrap className="explorer-view-controls">
      <Typography.Text type="secondary" className="explorer-assessment-count">
        参评 <strong>{batch.student_count}</strong> 人
      </Typography.Text>
      <Segmented
        aria-label="画像观察对象"
        value={view}
        options={[
          { label: '群体探索', value: 'group' },
          { label: '学生画像', value: 'student' },
        ]}
        onChange={(value) => {
          setPageSpace(null);
          setView(value === 'student' ? 'student' : 'group');
        }}
      />
    </Flex>
  );
  const openPortrait = (selected: ApiStudent) => {
    setPageSpace(null);
    setPortraitStudent(selected);
    setView('student');
    requestAnimationFrame(() =>
      document.getElementById('student-portrait-workspace')?.scrollIntoView({ block: 'start' }),
    );
  };
  return (
    <div className="page-stack explorer-page live-explorer">
      {summaryHost ? createPortal(viewControls, summaryHost) : viewControls}
      {origin && (
        <Alert
          type="info"
          showIcon
          title="正在查看原发现群体"
          description={`${group.data?.detail?.title ?? '正在核查发现'}。应用手动筛选将退出此原群体。`}
        />
      )}
      <Card
        size="small"
        className="explorer-query-panel"
        ref={queryPanel}
        style={view === 'student' && pageSpace ? { minHeight: pageSpace.queryHeight } : undefined}
      >
        <div className="filter-panel">
          <Space wrap size={[8, 8]} className="filter-builder">
            {draft.map((condition, index) => (
              <div className="filter-condition" key={index}>
                {index > 0 && <Tag className="and-token">且</Tag>}
                <Select
                  className="dimension-select"
                  aria-label={`筛选条件${index + 1}维度`}
                  value={condition.dimension_code}
                  options={DIMENSIONS.filter(
                    (d) => !draft.some((c, i) => i !== index && c.dimension_code === d.code),
                  ).map((d) => ({ value: d.code, label: d.name }))}
                  onChange={(dimension_code) =>
                    setDraft(draft.map((c, i) => (i === index ? { ...c, dimension_code } : c)))
                  }
                />
                <Select
                  className="operator-select"
                  aria-label={`筛选条件${index + 1}比较`}
                  value={condition.operator}
                  options={Object.entries(operatorLabels).map(([value, label]) => ({
                    value,
                    label,
                  }))}
                  onChange={(operator) =>
                    setDraft(draft.map((c, i) => (i === index ? { ...c, operator } : c)))
                  }
                />
                <InputNumber<string>
                  className="live-threshold"
                  inputMode="decimal"
                  aria-label={`筛选条件${index + 1}评分`}
                  stringMode
                  min="1"
                  max="5"
                  step="0.01"
                  controls={false}
                  changeOnBlur={false}
                  value={condition.value}
                  maxLength={32}
                  status={
                    validation && parseThreshold(condition.value) === null ? 'error' : undefined
                  }
                  formatter={(_, { userTyping, input }) => (userTyping ? input : condition.value)}
                  onInput={(value) => updateThreshold(index, value)}
                  onStep={(value) => updateThreshold(index, value)}
                  onPressEnter={apply}
                />
                <Button
                  type="text"
                  icon={<CloseOutlined />}
                  aria-label={`移除筛选条件${index + 1}`}
                  onClick={() => setDraft(draft.filter((_, i) => i !== index))}
                />
              </div>
            ))}
            {draft.length < 2 && (
              <Button
                onClick={() => {
                  const dimension = DIMENSIONS.find(
                    (d) => !draft.some((c) => c.dimension_code === d.code),
                  );
                  if (dimension)
                    setDraft([
                      ...draft,
                      { dimension_code: dimension.code, operator: 'gte', value: '2.75' },
                    ]);
                }}
              >
                添加条件
              </Button>
            )}
            <Button type="primary" onClick={apply}>
              应用筛选
            </Button>
            <Button
              onClick={() => {
                setPageSpace(null);
                setDraft([]);
                setConditions([]);
                setValidation(null);
                setOffset(0);
                setStudent(null);
                setPortraitStudent(null);
                setEvidenceId(null);
              }}
            >
              重置
            </Button>
            {unapplied && (
              <Tag color="warning" className="pending-filter-status" role="status">
                条件尚未应用
              </Tag>
            )}
          </Space>
          {validation && (
            <Alert className="session-notice" type="error" showIcon title={validation} />
          )}
        </div>
        <Flex vertical gap={tokens.sizeXXS / 2} className="match-summary">
          <Typography.Text>
            当前匹配 <strong>{group.data?.matched ?? '—'}</strong> / {group.data?.total ?? '—'} 人 ·
            占参评人数 <b className="blue-text">{ratioDisplay(group.data?.ratio ?? null)}</b>
          </Typography.Text>
          {(origin || conditions.length > 0) && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {origin
                ? `原发现条件：${originalConditions || '由受控指标参与记录确定'}；分析范围：${conditionText(conditions)}`
                : conditionText(conditions)}
            </Typography.Text>
          )}
        </Flex>
      </Card>
      {view === 'student' && (
        <div
          className="student-portrait-workspace"
          id="student-portrait-workspace"
          ref={portraitWorkspace}
          aria-busy={group.loading}
          style={pageSpace ? { minHeight: pageSpace.workspaceHeight } : undefined}
        >
          <Panel className="portrait-member-panel" title="匹配学生">
            {group.loading && <LoadingPanel />}
            {group.error && <RequestError error={group.error} retry={group.retry} />}
            {group.data && (
              <>
                {!group.data.items.length && <Empty description="没有匹配学生" />}
                <Select
                  className="portrait-member-select"
                  aria-label="选择当前页学生画像"
                  placeholder="选择学生"
                  value={
                    group.data.items.some((item) => item.student_id === portraitStudent?.student_id)
                      ? portraitStudent?.student_id
                      : undefined
                  }
                  options={group.data.items.map((item) => ({
                    value: item.student_id,
                    label: item.display_label,
                  }))}
                  onChange={(id) =>
                    setPortraitStudent(
                      group.data!.items.find((item) => item.student_id === id) ?? null,
                    )
                  }
                />
                <Menu
                  className="portrait-member-list"
                  aria-label="选择学生画像"
                  mode="inline"
                  selectedKeys={portraitStudent ? [portraitStudent.student_id] : []}
                  items={group.data.items.map((item) => ({
                    key: item.student_id,
                    label: item.display_label,
                  }))}
                  onSelect={({ key }) =>
                    setPortraitStudent(
                      group.data!.items.find((item) => item.student_id === key) ?? null,
                    )
                  }
                />
              </>
            )}
            {(group.data || (group.loading && pageSpace)) && (
              <Pagination
                simple
                current={offset / 10 + 1}
                pageSize={10}
                total={group.data?.matched ?? pageSpace!.matched}
                disabled={group.loading}
                hideOnSinglePage
                showSizeChanger={false}
                onChange={(page) => {
                  if (!group.data || page === offset / 10 + 1) return;
                  // Keep the document tall enough while the member list is replaced.
                  // Preserve this space on sparse final pages too, without restoring scroll later
                  // and overriding any scrolling the teacher does during the request.
                  setPageSpace({
                    workspaceHeight: portraitWorkspace.current?.getBoundingClientRect().height ?? 0,
                    queryHeight: queryPanel.current?.getBoundingClientRect().height ?? 0,
                    matched: group.data.matched,
                  });
                  setOffset((page - 1) * 10);
                  setStudent(null);
                  setEvidenceId(null);
                }}
              />
            )}
          </Panel>
          <StudentPortrait
            batch={batch}
            student={portraitStudent}
            reference={stats.data}
            referenceError={stats.error}
            retryReference={stats.retry}
            onEvidence={setEvidenceId}
            onRecords={setStudent}
          />
        </div>
      )}
      {view === 'group' && (
        <>
          <div className="explorer-grid">
            <Panel title={conditions.length && !origin ? '当前群体评分分布' : '能力组合分布'}>
              {origin && group.data?.detail && (
                <>
                  <p className="teacher-reading">{describeDiscovery(group.data.detail).numbers}</p>
                  <DiscoveryChart data={group.data.detail} />
                </>
              )}
              {rubric.loading && <LoadingPanel />}
              {rubric.error && <RequestError error={rubric.error} retry={rubric.retry} />}
              {!origin && conditions.length > 0 && group.data?.statistics && rubric.data && (
                <>
                  <ScoreReading
                    statistics={group.data.statistics}
                    rubric={rubric.data}
                    distribution
                    scopeCount={group.data.matched}
                  />
                  <ServerAbilityChart
                    statistics={group.data.statistics}
                    rubric={rubric.data}
                    mode="distribution"
                  />
                </>
              )}
              {!origin && conditions.length === 0 && (
                <>
                  {
                    <div className="axis-controls">
                      <Select
                        aria-label="热力图横轴"
                        value={axes.x}
                        options={DIMENSIONS.map((d) => ({
                          value: d.code,
                          label: `横轴 · ${d.name}`,
                        }))}
                        onChange={(x) => setAxes({ x, y: x === axes.y ? axes.x : axes.y })}
                      />
                      <Select
                        aria-label="热力图纵轴"
                        value={axes.y}
                        options={DIMENSIONS.filter((d) => d.code !== axes.x).map((d) => ({
                          value: d.code,
                          label: `纵轴 · ${d.name}`,
                        }))}
                        onChange={(y) => setAxes({ ...axes, y })}
                      />
                    </div>
                  }
                  {stats.loading && <LoadingPanel />}
                  {stats.error && <RequestError error={stats.error} retry={stats.retry} />}
                  {stats.data && (
                    <SparseMatrix
                      statistics={stats.data}
                      conditions={conditions}
                      onCell={selectCell}
                    />
                  )}
                </>
              )}
              {!origin && conditions.length > 0 && (
                <Disclosure
                  className="exploration-reference"
                  label={<>从全部测评评分组合中重新选择条件</>}
                >
                  {
                    <div className="axis-controls">
                      <Select
                        aria-label="热力图横轴"
                        value={axes.x}
                        options={DIMENSIONS.map((d) => ({
                          value: d.code,
                          label: `横轴 · ${d.name}`,
                        }))}
                        onChange={(x) => setAxes({ x, y: x === axes.y ? axes.x : axes.y })}
                      />
                      <Select
                        aria-label="热力图纵轴"
                        value={axes.y}
                        options={DIMENSIONS.filter((d) => d.code !== axes.x).map((d) => ({
                          value: d.code,
                          label: `纵轴 · ${d.name}`,
                        }))}
                        onChange={(y) => setAxes({ ...axes, y })}
                      />
                    </div>
                  }
                  {stats.loading && <LoadingPanel />}
                  {stats.error && <RequestError error={stats.error} retry={stats.retry} />}
                  {stats.data && (
                    <SparseMatrix
                      statistics={stats.data}
                      conditions={conditions}
                      onCell={selectCell}
                    />
                  )}
                </Disclosure>
              )}
            </Panel>
            <Panel title="群体评分概况" className="group-summary-card">
              {group.error && <RequestError error={group.error} retry={group.retry} />}
              <div className="group-distribution">
                {targetStats || distribution ? (
                  <Table
                    rowKey="dimension_code"
                    showHeader={false}
                    pagination={false}
                    dataSource={
                      targetStats
                        ? targetStats.map((d) => ({
                            dimension_code: d.dimension_code,
                            name: dimensionName(d.dimension_code),
                            mean: scoreText(d.target.mean),
                            validCount: d.target.valid_count,
                          }))
                        : distribution!.dimensions.map((d) => ({
                            dimension_code: d.dimension_code,
                            name: dimensionName(d.dimension_code),
                            mean: scoreDisplay(d.mean.value),
                            validCount: d.valid_count,
                          }))
                    }
                    columns={[
                      {
                        title: '维度',
                        dataIndex: 'name',
                      },
                      {
                        title: '均分',
                        dataIndex: 'mean',
                        render: (value: string) => (
                          <Typography.Text
                            strong
                            style={{ fontSize: 16, fontVariantNumeric: 'tabular-nums' }}
                          >
                            {value}
                          </Typography.Text>
                        ),
                      },
                      {
                        title: '有效人数',
                        dataIndex: 'validCount',
                        render: (count: number) => (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            有效 {count} 人
                          </Typography.Text>
                        ),
                      },
                    ]}
                  />
                ) : (
                  <Typography.Text type="secondary">
                    {group.loading ? '正在读取群体统计…' : '当前群体暂无可显示的统计。'}
                  </Typography.Text>
                )}
                <Disclosure className="exploration-reference" label={<>统计说明</>}>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    均值按全部匹配记录计算，缺失评分不记为 0。
                  </Typography.Text>
                </Disclosure>
              </div>
            </Panel>
          </div>
          <Panel title="匹配学生" subtitle="顺序不代表排名">
            {group.loading && <LoadingPanel />}
            {group.error && <RequestError error={group.error} retry={group.retry} />}
            {group.data && (
              <LiveStudentTable
                items={group.data.items}
                total={group.data.matched}
                offset={offset}
                onPage={(page) => {
                  setOffset(page);
                  setStudent(null);
                  setPortraitStudent(null);
                  setEvidenceId(null);
                }}
                onStudent={setStudent}
                onProfile={openPortrait}
                onEvidence={setEvidenceId}
              />
            )}
          </Panel>
          <AnalysisActions
            batch={batch}
            spec={{ snapshot_id: batch.snapshot_id, conditions, include_dimensions: true }}
            discovery={
              origin && group.data?.detail
                ? {
                    input_key: group.data.detail.scope.input_key,
                    discovery_ids: [origin.discovery_id],
                  }
                : undefined
            }
            blocked={
              origin && !group.data?.detail
                ? '正在核验原发现，请稍候或重试。'
                : unapplied
                  ? '筛选条件有未应用的修改，请先应用筛选再保存。'
                  : undefined
            }
          />
        </>
      )}
      <LiveStudentDrawer
        batch={batch}
        student={student}
        pageItems={group.data?.items ?? []}
        onStudent={setStudent}
        onClose={() => setStudent(null)}
      />
      <LiveScoreDrawer batch={batch} evidenceId={evidenceId} onClose={() => setEvidenceId(null)} />
    </div>
  );
}
