'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Divider,
  Empty,
  Flex,
  Pagination,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd';
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
import { LoadingPanel, NoBatch, RequestError } from './LiveStatus';
import { ServerAbilityChart } from './ServerCharts';
import { MetricEvidenceDrawer, type MetricSelection } from './LiveStudents';
import LiveDiscoveries from './LiveDiscoveries';
import AnalysisActions from './AnalysisActions';
import AssessmentItemPage from './AssessmentItemPage';
import AiInvestigationPanel from './AiInvestigationPanel';

export default function LiveInsightsPage() {
  const { batch } = useDataMode();
  if (!batch) return <NoBatch />;
  return batch.recognized_items ? (
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
          <div className="stat-grid assessment-overview" role="group" aria-label="当前测评概况">
            {[
              {
                label: '参评人数',
                value: stats.data?.statistics.scope.record_count ?? '—',
                unit: '人',
                icon: <TeamOutlined aria-hidden="true" />,
              },
              {
                label: '评分记录',
                value: batch.evaluation_count,
                unit: '条',
                icon: <FileTextOutlined aria-hidden="true" />,
              },
              {
                label: '能力维度',
                value: stats.data?.statistics.dimensions.length ?? '—',
                unit: '个',
                icon: <BarChartOutlined aria-hidden="true" />,
              },
            ].map(({ label, value, unit, icon }) => (
              <div className="live-stat" key={label}>
                <Statistic
                  title={label}
                  value={value}
                  prefix={icon}
                  suffix={unit}
                  valueRender={(node) => <strong>{node}</strong>}
                />
              </div>
            ))}
          </div>,
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
                <Alert type="info" showIcon title="AI 分析暂不可用，可继续查看评分统计。" />
              </Panel>
            )}
            <div className="insights-grid">
              <Panel
                title="评分画像"
                subtitle={conditions.length > 0 ? '全部参评者' : undefined}
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
                    <Flex vertical gap={tokens.sizeSM}>
                      <Flex
                        justify={conditions.length > 0 ? 'space-between' : 'end'}
                        align="center"
                        wrap
                        gap={tokens.sizeXS}
                      >
                        {conditions.length > 0 && (
                          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                            {conditionText(conditions)}
                          </Typography.Text>
                        )}
                        <Button size="small" onClick={insights.retry}>
                          刷新统计
                        </Button>
                      </Flex>
                      <Flex vertical gap={tokens.sizeSM}>
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
                      </Flex>
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
                      <Disclosure
                        className="muted small insight-comment-note"
                        label={<>评语分析说明</>}
                      >
                        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                          评语分析暂不可用。待审核的非空评语不等于空评语。
                        </Typography.Text>
                      </Disclosure>
                    </Flex>
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
      <Divider style={{ marginBlock: '4px 12px' }} />
      <Flex vertical gap={tokens.sizeSM}>
        <Flex align="center" justify="space-between" wrap gap={tokens.sizeXS}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            {card.dimension_code
              ? `${dimensionName(card.dimension_code)}评分分布`
              : '当前筛选群体概况'}
          </Typography.Title>
          <Space size="small">
            {card.status !== 'observed' && <Tag color="gold">数据不足</Tag>}
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              统计范围 {card.denominator} 人
            </Typography.Text>
          </Space>
        </Flex>
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
          <Flex vertical gap={tokens.sizeSM}>
            <Flex align="center" gap={tokens.sizeSM}>
              <label htmlFor="insight-score">选择评分</label>
              <Select
                id="insight-score"
                style={{ flex: 1, minWidth: 0 }}
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
            </Flex>
            <Row gutter={[tokens.sizeXS, tokens.sizeXS]}>
              <Col span={24}>
                <MetricTile card={card} metric={frequency} onMetric={onMetric} />
              </Col>
            </Row>
          </Flex>
        ) : (
          <Flex vertical gap={tokens.sizeSM}>
            <Row gutter={[tokens.sizeXS, tokens.sizeXS]}>
              {primary.map((metric) => (
                <Col xs={24} sm={12} key={metric.key}>
                  <MetricTile card={card} metric={metric} onMetric={onMetric} />
                </Col>
              ))}
            </Row>
            {quality.length > 0 && (
              <Space wrap size="small">
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
              </Space>
            )}
            {secondary.length > 0 && (
              <Disclosure
                className="secondary-metrics"
                label={<>更多统计指标 · {secondary.length} 项</>}
              >
                <Row gutter={[tokens.sizeXS, tokens.sizeXS]}>
                  {secondary.map((metric) => (
                    <Col xs={24} sm={12} key={metric.key}>
                      <MetricTile card={card} metric={metric} onMetric={onMetric} />
                    </Col>
                  ))}
                </Row>
              </Disclosure>
            )}
          </Flex>
        )}
        <Disclosure label={<>计算说明与使用限制</>}>
          <Flex vertical gap={tokens.sizeXS}>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {discoverySummaryText(card.summary)}
            </Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {card.suggested_next_action}
            </Typography.Text>
            {card.limitations.map((item) => (
              <Typography.Text key={item} type="secondary" style={{ fontSize: 13 }}>
                {item}
              </Typography.Text>
            ))}
          </Flex>
        </Disclosure>
      </Flex>
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
      block
      className="insight-metric-btn"
      onClick={() => onMetric(metric.key)}
      aria-label={`${card.title} ${metricLabel(metric)} 查看评分依据`}
    >
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {metricLabel(metric)}
      </Typography.Text>
      <Typography.Text strong style={{ fontSize: 18, fontVariantNumeric: 'tabular-nums' }}>
        {metricDisplay(metric)}
      </Typography.Text>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        查看评分依据 →
      </Typography.Text>
    </Button>
  );
}
