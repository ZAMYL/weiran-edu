'use client';
import { useCallback, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Button, Drawer, Empty, Pagination, Table, Tag } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import { discoverySummaryText } from '../../services/edu-api/discovery-view';
import type { DiscoveryDetail, DiscoverySpec } from '../../services/edu-api/discovery-types';
import type { ApiBatch, ApiCondition, ApiStudent } from '../../services/edu-api/types';
import { conditionText, dimensionName, operatorLabels, parseThreshold } from '../../services/edu-api/view-model';
import { DiscoveryChart, exactValue, scoreText } from './DiscoveryChart';
import { LiveScoreDrawer } from './LiveStudents';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';

const typeNames = {
  dimension_relation: '维度关联',
  group_difference: '群体差异',
  rare_combination: '少见评分组合',
  boundary_sensitivity: '边界比较',
};
const metricNames: Record<string, string> = {
  paired_scores: '配对评分学生',
  target_members: '选定群体',
  remainder_members: '其余学生',
  unevaluable_members: '无法判定的学生',
  rare_members: '少见组合学生',
  pair_reference: '完整配对参考学生',
  strict_members: '低于边界的学生',
  inclusive_members: '包含边界的学生',
  added_members: '边界新增学生',
};
function sourceName(key: string) {
  if (metricNames[key]) return metricNames[key];
  const [side, dim] = key.split('.');
  const codes: Record<string, string> = {
    ALGORITHMS: 'algorithm_data_structure',
    IMPLEMENTATION: 'code_implementation',
    MODELING: 'problem_modeling',
    EFFICIENCY: 'efficiency_awareness',
    STRATEGY: 'solution_strategy',
  };
  return dim && codes[dim]
    ? `${side === 'target' ? '选定群体' : '其余学生'} · ${dimensionName(codes[dim])}`
    : '查看该指标评分依据';
}
function metricsText(d: DiscoveryDetail) {
  switch (d.type) {
    case 'dimension_relation':
      return `配对学生 ${d.metrics.paired_count} 人；分析范围 ${d.metrics.student_denominator} 人；关联系数 ${d.metrics.rho?.value.toFixed(2) ?? '无法计算'}。`;
    case 'group_difference':
      return `选定群体 ${d.metrics.target_count} 人，其余学生 ${d.metrics.remainder_count} 人；分析范围 ${d.metrics.student_denominator} 人。比较指标：${dimensionName(d.metrics.outcome_dimension)}。`;
    case 'rare_combination':
      return `找到 ${d.metrics.match_count} 名学生；完整配对参考范围 ${d.metrics.pair_count} 人，占比 ${(exactValue(d.metrics.share_pairs) * 100).toFixed(1)}%。`;
    case 'boundary_sensitivity':
      return `低于边界 ${d.metrics.strict_count} 人，包含边界 ${d.metrics.inclusive_count} 人，新增 ${d.metrics.added_count} 人；分析范围 ${d.metrics.student_denominator} 人。`;
  }
}
function explorerConditions(scope: ApiCondition[], detail: DiscoveryDetail): ApiCondition[] | null {
  const merged = scope.map((condition) => ({ ...condition }));
  for (const condition of detail.conditions) {
    const value = parseThreshold(condition.threshold.display);
    // A source query can express overlapping or more-than-two AND constraints;
    // the current explorer cannot. Never silently discard a source condition.
    if (
      value === null ||
      Math.abs(value - condition.threshold.numerator / condition.threshold.denominator) > 1e-10 ||
      merged.some((current) => current.dimension_code === condition.dimension_code)
    )
      return null;
    merged.push({ dimension_code: condition.dimension_code, operator: condition.operator, value });
  }
  return merged.length <= 2 ? merged : null;
}
export default function LiveDiscoveries({ batch }: { batch: ApiBatch }) {
  const { client, conditions } = useDataMode();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const spec: DiscoverySpec = useMemo(
    () => ({
      snapshot_id: batch.snapshot_id,
      profile: 'edu.discovery.profile.v1',
      scope_conditions: conditions.map((c) => ({ ...c })),
    }),
    [batch.snapshot_id, conditions],
  );
  const load = useCallback(
    (signal: AbortSignal) => client.discoveries(batch, spec, offset, signal),
    [client, batch, spec, offset],
  );
  const resource = useRemote(
    `discoveries:${batch.batch_id}:${JSON.stringify(spec)}:${offset}`,
    load,
  );
  return (
    <Panel
      title="主动学情发现"
      subtitle="从当前测评评分中探索值得教师核查的现象。"
      action={<Button onClick={resource.retry}>刷新发现</Button>}
    >
      <p>分析范围：{conditionText(spec.scope_conditions)}</p>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && (
        <>
          <p data-testid="discovery-scope">
            当前范围 {resource.data.scope.scope_record_count} 人 · 本次测评{' '}
            {resource.data.scope.batch_record_count} 人
          </p>
          {resource.data.items.length ? (
            resource.data.items.map((card) => (
              <section
                className="panel live-candidate"
                key={card.discovery_id}
                data-testid="discovery-card"
              >
                <Tag color="blue">{typeNames[card.type]}</Tag>
                {card.status !== 'displayable' && <Tag>需谨慎解释</Tag>}
                <h3>{card.title}</h3>
                <p>{discoverySummaryText(card.summary)}</p>
                <Button onClick={() => setSelected(card.discovery_id)}>查看发现与依据</Button>
              </section>
            ))
          ) : (
            <Empty description="当前范围没有符合条件的发现，可调整筛选后重试。" />
          )}
          {resource.data.truncated && (
            <p className="muted small">
              符合条件的发现较多，本次展示排序靠前的 {resource.data.retained_count} 项。
            </p>
          )}
          <Pagination
            current={offset / 10 + 1}
            total={resource.data.retained_count}
            pageSize={10}
            showSizeChanger={false}
            hideOnSinglePage
            onChange={(p) => setOffset((p - 1) * 10)}
            showTotal={(n) => `共 ${n} 项发现`}
          />
        </>
      )}
      {selected && resource.data && (
        <DiscoveryDetailDrawer
          key={selected}
          batch={batch}
          spec={spec}
          discoveryId={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </Panel>
  );
}
function DiscoveryDetailDrawer({
  batch,
  spec,
  discoveryId,
  onClose,
}: {
  batch: ApiBatch;
  spec: DiscoverySpec;
  discoveryId: string;
  onClose: () => void;
}) {
  const { client, setConditions } = useDataMode();
  const router = useRouter();
  const [metric, setMetric] = useState<string | null>(null);
  const load = useCallback(
    async (signal: AbortSignal) => {
      // Sequential requests respect the backend's per-worker two-slot budget.
      const detail = await client.discoveryDetail(batch, discoveryId, spec, signal);
      const chart = await client.discoveryChart(batch, discoveryId, spec, signal);
      if (
        chart.input_key !== detail.input_key ||
        chart.rule_version !== detail.rule_version ||
        chart.type !== detail.type ||
        JSON.stringify(chart.chart) !== JSON.stringify(detail.chart) ||
        JSON.stringify(chart.conditions) !== JSON.stringify(detail.conditions) ||
        JSON.stringify(chart.metrics) !== JSON.stringify(detail.metrics)
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return { detail, chart };
    },
    [client, batch, discoveryId, spec],
  );
  const resource = useRemote(`discovery-detail:${discoveryId}:${JSON.stringify(spec)}`, load);
  const d = resource.data?.detail;
  const readyToExplore = d ? explorerConditions(spec.scope_conditions, d) : null;
  return (
    <>
      <Drawer title="学情发现详情" open onClose={onClose} size={820}>
        {resource.loading && <LoadingPanel />}
        {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
        {d && resource.data && (
          <>
            <h2>{d.title}</h2>
            <p>分析范围：{conditionText(spec.scope_conditions)}</p>
            {d.conditions.length > 0 && (
              <p>
                发现条件：
                {d.conditions
                  .map(
                    (c) =>
                      `${dimensionName(c.dimension_code)} ${operatorLabels[c.operator]} ${scoreText(c.threshold)}`,
                  )
                  .join(' 且 ')}
              </p>
            )}
            <p data-testid="discovery-metrics">{metricsText(d)}</p>
            <DiscoveryChart data={resource.data.chart} />
            {d.type === 'group_difference' && (
              <Table
                size="small"
                rowKey="dimension_code"
                dataSource={d.chart}
                pagination={false}
                scroll={{ x: 550 }}
                columns={[
                  {
                    title: '维度',
                    render: (_, row) =>
                      `${dimensionName(row.dimension_code)}${row.condition_defined ? '（筛选维度）' : ''}`,
                  },
                  { title: '群体均值', render: (_, row) => scoreText(row.target.mean) },
                  { title: '其余学生均值', render: (_, row) => scoreText(row.remainder.mean) },
                  { title: '均值差', render: (_, row) => scoreText(row.difference) },
                ]}
              />
            )}
            {readyToExplore && (
              <Button
                type="primary"
                onClick={() => {
                  setConditions(readyToExplore);
                  onClose();
                  router.push('/explorer');
                }}
              >
                按发现条件探索
              </Button>
            )}
            {d.type === 'group_difference' && !readyToExplore && (
              <p className="muted small">
                当前组合超出画像探索的两条件限制，请通过评分依据查看原始群体。
              </p>
            )}
            <h3>查看学生与评分依据</h3>
            <div className="live-metrics">
              {d.controlled_queries.map((q) => (
                <Button key={q.metric_key} onClick={() => setMetric(q.metric_key)}>
                  {sourceName(q.metric_key)}
                </Button>
              ))}
            </div>
            {d.limitations.length > 0 && (
              <details>
                <summary>解释与使用边界</summary>
                {d.limitations.map((v) => (
                  <p key={v}>{v}</p>
                ))}
              </details>
            )}
            <p className="muted small">
              发现是评分现象，需结合教学情境核查。可在画像探索中调整分析范围。
            </p>
          </>
        )}
      </Drawer>
      {metric && (
        <DiscoveryRecordsDrawer
          key={metric}
          batch={batch}
          spec={spec}
          discoveryId={discoveryId}
          metric={metric}
          onClose={() => setMetric(null)}
        />
      )}
    </>
  );
}
function DiscoveryRecordsDrawer({
  batch,
  spec,
  discoveryId,
  metric,
  onClose,
}: {
  batch: ApiBatch;
  spec: DiscoverySpec;
  discoveryId: string;
  metric: string;
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const [offset, setOffset] = useState(0);
  const [evidence, setEvidence] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) =>
      client.discoveryRecords(batch, discoveryId, metric, spec, offset, signal),
    [client, batch, discoveryId, metric, spec, offset],
  );
  const resource = useRemote(
    `discovery-records:${discoveryId}:${metric}:${JSON.stringify(spec)}:${offset}`,
    load,
  );
  const result = resource.data;
  return (
    <>
      <Drawer title="发现群体与评分证据" open onClose={onClose} size={800}>
        <p>{sourceName(metric)}</p>
        {resource.loading && <LoadingPanel />}
        {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
        {result && (
          <>
            <p data-testid="discovery-record-count">找到 {result.total_count} 名学生</p>
            <Table<ApiStudent>
              rowKey="student_id"
              size="small"
              dataSource={result.items}
              pagination={false}
              scroll={{ x: 500 }}
              columns={[
                { title: '匿名学生', dataIndex: 'display_label' },
                ...result.source_dimensions.map((dimension) => ({
                  title: dimensionName(dimension),
                  key: dimension,
                  render: (_: unknown, student: ApiStudent) => {
                    const score = student.scores.find((s) => s.dimension_code === dimension);
                    return score ? (
                      <Button
                        type="link"
                        onClick={() => setEvidence(score.evidence_id)}
                        aria-label={`${student.display_label} ${dimensionName(dimension)} 评分证据`}
                      >
                        {score.score.toFixed(2)} 分
                      </Button>
                    ) : (
                      '—'
                    );
                  },
                })),
              ]}
            />
            <Pagination
              current={offset / 10 + 1}
              total={result.total_count}
              pageSize={10}
              showSizeChanger={false}
              hideOnSinglePage
              onChange={(p) => {
                setOffset((p - 1) * 10);
                setEvidence(null);
              }}
            />
          </>
        )}
      </Drawer>
      <LiveScoreDrawer
        key={evidence}
        batch={batch}
        evidenceId={evidence}
        onClose={() => setEvidence(null)}
      />
    </>
  );
}
