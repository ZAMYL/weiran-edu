'use client';
import { useCallback, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';
import { useRouter } from 'next/navigation';
import { Button, Checkbox, Empty, Pagination, Segmented, Select, Tag } from 'antd';
import { TeamOutlined, FileTextOutlined, BarChartOutlined } from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import {
  DIMENSIONS,
  type ApiBatch,
  type InsightCard,
  type InsightMetric,
  type InsightSpec,
} from '../../services/edu-api/types';
import {
  conditionText,
  dimensionName,
  metricDisplay,
  parseThreshold,
  scoreDisplay,
} from '../../services/edu-api/view-model';
import { discoverySummaryText } from '../../services/edu-api/discovery-view';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import { ServerAbilityChart } from './ServerCharts';
import { MetricEvidenceDrawer, type MetricSelection } from './LiveStudents';
import LiveDataPage from './LiveDataPage';
import LiveDiscoveries from './LiveDiscoveries';
import AnalysisActions from './AnalysisActions';
import AssessmentItemPage from './AssessmentItemPage';
import AiInvestigationPanel from './AiInvestigationPanel';

export default function LiveInsightsPage() {
  const { batch, assessmentPanel, assessmentRevision } = useDataMode();
  return assessmentPanel || !batch ? (
    <LiveDataPage
      key={`${assessmentPanel}:${assessmentRevision}`}
      view={assessmentPanel ?? 'upload'}
    />
  ) : batch.recognized_items ? (
    <AssessmentItemPage key={batch.snapshot_id} batch={batch} view="diagnosis" />
  ) : (
    <Insights key={`${batch.batch_id}:${batch.snapshot_id}`} batch={batch} />
  );
}
function Insights({ batch }: { batch: ApiBatch }) {
  const { client, conditions, syntheticAi, liveAi } = useDataMode();
  const router = useRouter();
  const { summaryHost } = useWorkspaceLayout();
  const contextKey = `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
  const loadAiCapability = useCallback(
    (signal: AbortSignal) => client.aiCapability(signal),
    [client],
  );
  const aiCapability = useRemote(
    liveAi && !syntheticAi ? 'ai-live-capability' : null,
    loadAiCapability,
  );
  const canRunAi =
    syntheticAi || (aiCapability.data?.available && aiCapability.data.mode === 'qwen_live');
  const [rightView, setRightView] = useState<'focus' | 'statistics'>('focus');
  const analysisSwitch = (
    <Segmented
      aria-label="右侧分析内容"
      value={rightView}
      options={[
        { label: '重点关注', value: 'focus' },
        { label: '评分统计', value: 'statistics' },
      ]}
      onChange={(value) => setRightView(value === 'statistics' ? 'statistics' : 'focus')}
    />
  );
  const [mode, setMode] = useState<'radar' | 'distribution'>('radar');
  const [includeDimensions, setIncludeDimensions] = useState(true);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<MetricSelection | null>(null);
  const [aspect, setAspect] = useState(conditions.length ? 'group' : 'algorithm_data_structure');
  const spec: InsightSpec = useMemo(
    () => ({
      snapshot_id: batch.snapshot_id,
      conditions: conditions.map((c) => ({ ...c })),
      include_dimensions: includeDimensions,
    }),
    [batch.snapshot_id, conditions, includeDimensions],
  );
  const loadStatistics = useCallback(
    async (signal: AbortSignal) => {
      const [statistics, rubric] = await Promise.all([
        client.statistics(batch, undefined, undefined, signal),
        client.rubric(batch, signal),
      ]);
      return { statistics, rubric };
    },
    [client, batch],
  );
  const stats = useRemote(
    `insight-stats:${batch.batch_id}:${batch.snapshot_id}`,
    loadStatistics,
    client.overviewCache,
  );
  const loadInsights = useCallback(
    (signal: AbortSignal) => client.insights(batch, spec, offset, signal),
    [client, batch, spec, offset],
  );
  const insights = useRemote(
    rightView === 'statistics' || selection
      ? `insights:${batch.batch_id}:${JSON.stringify(spec)}:${offset}`
      : null,
    loadInsights,
  );
  const openMetric = (card: InsightCard, metricKey: string) =>
    setSelection({
      candidateId: card.candidate_id,
      metricKey,
      title: card.title,
      spec: { ...spec, conditions: spec.conditions.map((c) => ({ ...c })) },
    });
  const cards = [...(insights.data?.items ?? [])].sort(
    (left, right) =>
      DIMENSIONS.findIndex((dimension) => dimension.code === left.dimension_code) -
      DIMENSIONS.findIndex((dimension) => dimension.code === right.dimension_code),
  );
  const card =
    cards.find((candidate) => (candidate.dimension_code ?? 'group') === aspect) ?? cards[0];
  return (
    <div className="page-stack live-insights">
      {summaryHost &&
        createPortal(
          <dl className="stat-grid assessment-overview" aria-label="当前测评概况">
            {[
              {
                label: '参评人数',
                value: stats.data?.statistics.scope.record_count ?? null,
                unit: '人',
                icon: <TeamOutlined />,
              },
              {
                label: '评分记录',
                value: batch.evaluation_count,
                unit: '条',
                icon: <FileTextOutlined />,
              },
              {
                label: '能力维度',
                value: stats.data?.statistics.dimensions.length ?? null,
                unit: '个',
                icon: <BarChartOutlined />,
              },
            ].map(({ label, value, unit, icon }) => (
              <div className="live-stat" key={label}>
                <dt>
                  <span className="overview-icon" aria-hidden="true">
                    {icon}
                  </span>
                  {label}
                </dt>
                <dd>
                  <strong>{value ?? '—'}</strong>
                  <span>{unit}</span>
                </dd>
              </div>
            ))}
          </dl>,
          summaryHost,
        )}
      <LiveDiscoveries
        key={contextKey}
        batch={batch}
        focusAction={analysisSwitch}
        onShowStatistics={() => setRightView('statistics')}
      >
        {(focusPanel) => (
          <>
            {canRunAi ? (
              <AiInvestigationPanel batch={batch} />
            ) : (
              <Panel title="AI 分析" className="ai-investigation-panel">
                <p className="muted">AI 分析暂不可用，可继续查看评分统计。</p>
              </Panel>
            )}
            <div className="insights-grid">
              <Panel
                title="评分画像"
                subtitle="全部参评者 · 有效评分均值"
                action={
                  <Segmented
                    options={[
                      { label: '能力画像', value: 'radar' },
                      { label: '评分分布', value: 'distribution' },
                    ]}
                    value={mode}
                    onChange={(value) => setMode(value as 'radar' | 'distribution')}
                  />
                }
                className="radar-panel"
              >
                {stats.loading && <LoadingPanel />}
                {stats.error && <RequestError error={stats.error} retry={stats.retry} />}
                {stats.data && (
                  <>
                    <ServerAbilityChart
                      statistics={stats.data.statistics}
                      rubric={stats.data.rubric}
                      mode={mode}
                    />
                    <Button type="link" onClick={() => router.push('/explorer')}>
                      进入画像探索 →
                    </Button>
                  </>
                )}
              </Panel>
              <div className="insights-right">
                <div hidden={rightView !== 'focus'}>{focusPanel}</div>
                <div hidden={rightView !== 'statistics'}>
                  <Panel
                    title="学情分析"
                    className="insights-statistics-panel"
                    action={analysisSwitch}
                  >
                    <div className="discovery-focus-toolbar">
                      <p className="muted small">{conditionText(conditions)}</p>
                      <Button size="small" onClick={insights.retry}>
                        刷新统计
                      </Button>
                    </div>
                    <div className="live-insight-controls">
                      <Select
                        id="insight-aspect"
                        aria-label="查看洞察方面"
                        value={card ? (card.dimension_code ?? 'group') : undefined}
                        placeholder="暂无可查看的方面"
                        disabled={!cards.length || insights.loading}
                        options={cards.map((candidate) => ({
                          value: candidate.dimension_code ?? 'group',
                          label: candidate.dimension_code
                            ? dimensionName(candidate.dimension_code)
                            : '当前筛选群体',
                        }))}
                        onChange={(value) => {
                          setAspect(value);
                          setSelection(null);
                        }}
                      />
                      <Checkbox
                        checked={includeDimensions}
                        onChange={(e) => {
                          setIncludeDimensions(e.target.checked);
                          setOffset(0);
                        }}
                      >
                        包含各维度洞察
                      </Checkbox>
                    </div>
                    {insights.loading && <LoadingPanel />}
                    {insights.error && (
                      <RequestError error={insights.error} retry={insights.retry} />
                    )}
                    {insights.data && (
                      <>
                        {card ? (
                          <CandidateView
                            key={card.candidate_id}
                            card={card}
                            onMetric={(key) => openMetric(card, key)}
                          />
                        ) : (
                          <Empty description="当前查询没有可展示的评分洞察候选。" />
                        )}
                        <Pagination
                          current={Math.floor(offset / 10) + 1}
                          total={insights.data.total_count}
                          pageSize={10}
                          hideOnSinglePage
                          showSizeChanger={false}
                          onChange={(page) => setOffset((page - 1) * 10)}
                          showTotal={(total) => `共 ${total} 张候选卡`}
                        />
                      </>
                    )}
                    <details className="muted small insight-comment-note">
                      <summary>评语分析说明</summary>
                      <p>评语分析暂不可用。待审核的非空评语不等于空评语。</p>
                    </details>
                  </Panel>
                </div>
              </div>
            </div>
          </>
        )}
      </LiveDiscoveries>
      <AnalysisActions batch={batch} spec={spec} />
      <MetricEvidenceDrawer
        key={`${selection?.candidateId}:${selection?.metricKey}`}
        batch={batch}
        selection={selection}
        onClose={() => setSelection(null)}
      />
    </div>
  );
}

const qualityKeys = new Set(['valid_count', 'missing_count', 'invalid_count']);
const metricLabels: Record<string, string> = {
  mean: '测评平均分',
  median: '中位数',
  minimum: '最低分',
  maximum: '最高分',
  matched_count: '匹配人数',
  matched_ratio: '匹配比例',
  eligible_count: '可判定人数',
  unevaluable_count: '不可判定人数',
  valid_count: '有效人数',
  missing_count: '缺失人数',
  invalid_count: '无效人数',
};
function metricLabel(metric: InsightMetric) {
  return metric.key.startsWith('frequency:')
    ? `${scoreDisplay(metric.at_score?.value ?? null)} 分人数`
    : (metricLabels[metric.key] ?? metric.key);
}
function CandidateView({ card, onMetric }: { card: InsightCard; onMetric: (key: string) => void }) {
  const [content, setContent] = useState('summary');
  const [scoreKey, setScoreKey] = useState('');
  const frequencies = card.metrics.filter((metric) => metric.key.startsWith('frequency:'));
  const frequency = frequencies.find((metric) => metric.key === scoreKey) ?? frequencies[0];
  const summary = card.metrics.filter(
    (metric) => !metric.key.startsWith('frequency:') && !qualityKeys.has(metric.key),
  );
  const quality = card.metrics.filter((metric) => qualityKeys.has(metric.key));
  const primaryKeys = new Set(['mean', 'median', 'matched_count', 'matched_ratio']);
  const primary = summary.filter((metric) => primaryKeys.has(metric.key));
  const secondary = summary.filter((metric) => !primaryKeys.has(metric.key));
  return (
    <section className="live-candidate">
      <div className="finding-top">
        {card.status !== 'observed' && <Tag color="gold">数据不足</Tag>}
        <small>统计范围 {card.denominator} 人</small>
      </div>
      <h2>
        {card.dimension_code ? `${dimensionName(card.dimension_code)}评分分布` : '当前筛选群体概况'}
      </h2>
      {frequencies.length > 0 && (
        <Segmented
          aria-label="洞察内容"
          options={[
            { label: '统计概况', value: 'summary' },
            { label: '评分分布', value: 'frequency' },
          ]}
          value={content}
          onChange={setContent}
        />
      )}
      {content === 'frequency' && frequency ? (
        <>
          <div className="insight-score-picker">
            <label htmlFor="insight-score">选择评分</label>
            <Select
              id="insight-score"
              aria-label="查看评分人数"
              showSearch={{
                filterOption: (input, option) => {
                  const score = parseThreshold(input.trim().replace(/\s*分$/, ''));
                  return Boolean(
                    option?.label.includes(input) ||
                    (score !== null && option?.label === `${scoreDisplay(score)} 分`),
                  );
                },
              }}
              value={frequency.key}
              options={frequencies.map((metric) => ({
                value: metric.key,
                label: `${scoreDisplay(metric.at_score?.value ?? null)} 分`,
              }))}
              onChange={setScoreKey}
            />
          </div>
          <div className="live-metrics live-frequency">
            <MetricTile card={card} metric={frequency} onMetric={onMetric} />
          </div>
        </>
      ) : (
        <>
          <div className="live-metrics">
            {primary.map((metric) => (
              <MetricTile key={metric.key} card={card} metric={metric} onMetric={onMetric} />
            ))}
          </div>
          {quality.length > 0 && (
            <div className="live-quality">
              {quality.map((metric) => (
                <Button
                  key={metric.key}
                  size="small"
                  aria-label={`${card.title} ${metricLabel(metric)} 查看评分依据`}
                  onClick={() => onMetric(metric.key)}
                >
                  {metricLabels[metric.key]} {metricDisplay(metric)}
                </Button>
              ))}
            </div>
          )}
          {secondary.length > 0 && (
            <details className="secondary-metrics">
              <summary>更多统计指标 · {secondary.length} 项</summary>
              <div className="live-metrics">
                {secondary.map((metric) => (
                  <MetricTile key={metric.key} card={card} metric={metric} onMetric={onMetric} />
                ))}
              </div>
            </details>
          )}
        </>
      )}
      <details>
        <summary>计算说明与使用限制</summary>
        <p className="small">{discoverySummaryText(card.summary)}</p>
        <p className="small">{card.suggested_next_action}</p>
        {card.limitations.map((item) => (
          <p key={item} className="small">
            {item}
          </p>
        ))}
      </details>
    </section>
  );
}
function MetricTile({
  card,
  metric,
  onMetric,
}: {
  card: InsightCard;
  metric: InsightMetric;
  onMetric: (key: string) => void;
}) {
  return (
    <Button
      onClick={() => onMetric(metric.key)}
      aria-label={`${card.title} ${metricLabel(metric)} 查看评分依据`}
    >
      <span>{metricLabel(metric)}</span>
      <b>{metricDisplay(metric)}</b>
      <small>查看评分依据 →</small>
    </Button>
  );
}
