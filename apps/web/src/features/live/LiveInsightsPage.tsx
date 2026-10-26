'use client';
import { useCallback, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Checkbox, Empty, Pagination, Segmented, Tag } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { type ApiBatch, type InsightCard, type InsightSpec } from '../../services/edu-api/types';
import { conditionText, metricDisplay } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, NoBatch, RequestError } from './LiveStatus';
import { ServerAbilityChart } from './ServerCharts';
import { MetricEvidenceDrawer, type MetricSelection } from './LiveStudents';
import LiveDiscoveries from './LiveDiscoveries';
import AnalysisActions from './AnalysisActions';

export default function LiveInsightsPage() {
  const { batch } = useDataMode();
  return batch ? <Insights batch={batch} /> : <NoBatch />;
}
function Insights({ batch }: { batch: ApiBatch }) {
  const { client, conditions } = useDataMode();
  const router = useRouter();
  const [mode, setMode] = useState<'radar' | 'distribution'>('radar');
  const [includeDimensions, setIncludeDimensions] = useState(true);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<MetricSelection | null>(null);
  const [expandedCards, setExpandedCards] = useState<string[]>([]);
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
  const stats = useRemote(`insight-stats:${batch.batch_id}:${batch.snapshot_id}`, loadStatistics);
  const loadInsights = useCallback(
    (signal: AbortSignal) => client.insights(batch, spec, offset, signal),
    [client, batch, spec, offset],
  );
  const insights = useRemote(
    `insights:${batch.batch_id}:${JSON.stringify(spec)}:${offset}`,
    loadInsights,
  );
  const openMetric = (card: InsightCard, metricKey: string) =>
    setSelection({
      candidateId: card.candidate_id,
      metricKey,
      title: card.title,
      spec: { ...spec, conditions: spec.conditions.map((c) => ({ ...c })) },
    });
  return (
    <div className="page-stack live-insights">
      <AnalysisActions batch={batch} spec={spec} />
      <div className="stat-grid">
        {[
          ['参评人数', stats.data?.statistics.scope.record_count ?? null, '人'],
          ['评价记录', batch.evaluation_count, '条'],
          ['能力维度', stats.data?.statistics.dimensions.length ?? null, '个'],
        ].map(([label, value, unit]) => (
          <Panel className="live-stat" key={label}>
            <p className="muted">{label}</p>
            <strong>{value ?? '—'}</strong>
            <span>{unit}</span>
          </Panel>
        ))}
      </div>
      <div className="insights-grid">
        <Panel
          title="本次测评概况"
          subtitle="测评平均分 · 基于各维度有效评分"
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
              <div className="chart-caption">按本次测评的有效评分计算，保留原始小数精度。</div>
              <Button type="link" onClick={() => router.push('/explorer')}>
                进入画像探索 →
              </Button>
            </>
          )}
        </Panel>
        <div className="insights-right">
          <LiveDiscoveries
            key={`${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`}
            batch={batch}
          />
          <Panel
            title="测评洞察"
            subtitle="系统生成的候选发现，需结合评分证据核验。"
            action={<Button onClick={insights.retry}>刷新洞察</Button>}
          >
            <div className="live-insight-controls">
              <Checkbox
                checked={includeDimensions}
                onChange={(e) => {
                  setIncludeDimensions(e.target.checked);
                  setOffset(0);
                }}
              >
                包含各维度洞察
              </Checkbox>
              <p className="muted small">当前查询：{conditionText(conditions)}</p>
            </div>
            {insights.loading && <LoadingPanel />}
            {insights.error && <RequestError error={insights.error} retry={insights.retry} />}
            {insights.data && (
              <>
                {insights.data.items.length ? (
                  insights.data.items.map((card, index) => (
                    <section
                      className={
                        index === 0 ? 'finding-card live-candidate' : 'panel live-candidate'
                      }
                      key={card.candidate_id}
                    >
                      <div className="finding-top">
                        <Tag color="blue">
                          {card.status === 'observed' ? '评分观察' : '数据不足'}
                        </Tag>
                        <small>统计范围 {card.denominator} 人</small>
                      </div>
                      <h2>{card.title}</h2>
                      <p>{card.summary}</p>
                      <div className="live-metrics">
                        {(expandedCards.includes(card.candidate_id)
                          ? card.metrics
                          : card.metrics.slice(0, 8)
                        ).map((metric) => (
                          <Button
                            key={metric.key}
                            onClick={() => openMetric(card, metric.key)}
                            aria-label={`${card.title} ${metric.key} 查看评分证据`}
                          >
                            <span>
                              {metric.key.startsWith('frequency:')
                                ? `${metric.at_score?.display ?? ''} 分人数`
                                : ((
                                    {
                                      mean: '测评平均分',
                                      median: '中位数',
                                      minimum: '最低分',
                                      maximum: '最高分',
                                      matched_count: '匹配人数',
                                      matched_ratio: '匹配比例',
                                      valid_count: '有效人数',
                                      missing_count: '缺失人数',
                                      invalid_count: '无效人数',
                                    } as Record<string, string>
                                  )[metric.key] ?? metric.key)}
                            </span>
                            <b>{metricDisplay(metric)}</b>
                            <small>查看评分证据 →</small>
                          </Button>
                        ))}
                      </div>
                      {card.metrics.length > 8 && (
                        <Button
                          type="link"
                          onClick={() =>
                            setExpandedCards((current) =>
                              current.includes(card.candidate_id)
                                ? current.filter((id) => id !== card.candidate_id)
                                : [...current, card.candidate_id],
                            )
                          }
                        >
                          {expandedCards.includes(card.candidate_id)
                            ? '收起评分指标'
                            : `查看全部 ${card.metrics.length} 项评分指标`}
                        </Button>
                      )}
                      <p className="small">{card.suggested_next_action}</p>
                      <details>
                        <summary>解释边界与使用限制</summary>
                        {card.limitations.map((item) => (
                          <p key={item} className="small">
                            {item}
                          </p>
                        ))}
                      </details>
                    </section>
                  ))
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
          </Panel>
          <Alert
            showIcon
            type="info"
            title="评语分析暂不可用"
            description="当前未提供评语分析。待审核的非空评语不等于空评语。"
          />
        </div>
      </div>
      <MetricEvidenceDrawer
        key={`${selection?.candidateId}:${selection?.metricKey}`}
        batch={batch}
        selection={selection}
        onClose={() => setSelection(null)}
      />
    </div>
  );
}
