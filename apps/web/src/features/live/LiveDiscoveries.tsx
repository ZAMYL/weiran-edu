'use client';
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { Checkbox, Button, Drawer, Empty, Pagination, Select, Table, Tag } from 'antd';
import { useRouter } from 'next/navigation';
import { ReloadOutlined } from '@ant-design/icons';
import type { DiscoverySelection } from '../../services/edu-api/analysis-types';
import { discoveryUrl, originForDiscovery } from '../../services/edu-api/discovery-navigation';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import { discoverySummaryText } from '../../services/edu-api/discovery-view';
import type { DiscoveryDetail, DiscoverySpec } from '../../services/edu-api/discovery-types';
import type { ApiBatch, ApiStudent } from '../../services/edu-api/types';
import {
  conditionText,
  dimensionName,
  operatorLabels,
  scoreDisplay,
  ratioDisplay,
} from '../../services/edu-api/view-model';
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
      return `配对学生 ${d.metrics.paired_count} 人；分析范围 ${d.metrics.student_denominator} 人；关联系数 ${d.metrics.rho ? scoreDisplay(d.metrics.rho.value) : '无法计算'}。`;
    case 'group_difference':
      return `选定群体 ${d.metrics.target_count} 人，其余学生 ${d.metrics.remainder_count} 人；分析范围 ${d.metrics.student_denominator} 人。比较指标：${dimensionName(d.metrics.outcome_dimension)}。`;
    case 'rare_combination':
      return `找到 ${d.metrics.match_count} 名学生；完整配对参考范围 ${d.metrics.pair_count} 人，占比 ${ratioDisplay(exactValue(d.metrics.share_pairs))}。`;
    case 'boundary_sensitivity':
      return `低于边界 ${d.metrics.strict_count} 人，包含边界 ${d.metrics.inclusive_count} 人，新增 ${d.metrics.added_count} 人；分析范围 ${d.metrics.student_denominator} 人。`;
  }
}
function coverage(detail: DiscoveryDetail) {
  switch (detail.type) {
    case 'dimension_relation':
      return `参与配对 ${detail.metrics.paired_count} 人 / 分析范围 ${detail.metrics.student_denominator} 人`;
    case 'group_difference':
      return `选定群体 ${detail.metrics.target_count} 人 · 其余群体 ${detail.metrics.remainder_count} 人`;
    case 'rare_combination':
      return `少见组合 ${detail.metrics.match_count} 人 / 配对参考 ${detail.metrics.pair_count} 人`;
    case 'boundary_sensitivity':
      return `边界新增 ${detail.metrics.added_count} 人 · ${detail.metrics.strict_count} 人 → ${detail.metrics.inclusive_count} 人`;
  }
}
function FocusMetrics({ detail }: { detail: DiscoveryDetail }) {
  let metrics: { label: string; value: string; unit?: string; note?: string }[];
  switch (detail.type) {
    case 'group_difference': {
      const row = detail.chart.find(
        (item) => item.dimension_code === detail.metrics.outcome_dimension,
      );
      const difference = row?.difference ?? null;
      metrics = [
        {
          label: `选定群体 · ${detail.metrics.target_count} 人`,
          value: scoreText(row?.target.mean ?? null),
          unit: '分',
          note:
            row && row.target.valid_count !== detail.metrics.target_count
              ? `有效评分 ${row.target.valid_count} 人`
              : undefined,
        },
        {
          label: `其余群体 · ${detail.metrics.remainder_count} 人`,
          value: scoreText(row?.remainder.mean ?? null),
          unit: '分',
          note:
            row && row.remainder.valid_count !== detail.metrics.remainder_count
              ? `有效评分 ${row.remainder.valid_count} 人`
              : undefined,
        },
        {
          label: '均分差',
          value: `${difference && exactValue(difference) > 0 ? '+' : ''}${scoreText(difference)}`,
          unit: '分',
          note: '选定减其余',
        },
      ];
      break;
    }
    case 'dimension_relation':
      metrics = [
        {
          label: '评分关联系数',
          value: detail.metrics.rho ? scoreDisplay(detail.metrics.rho.value) : '—',
        },
        { label: '参与配对', value: String(detail.metrics.paired_count), unit: '人' },
        { label: '未配对', value: String(detail.metrics.excluded_count), unit: '人' },
      ];
      break;
    case 'rare_combination':
      metrics = [
        { label: '少见组合', value: String(detail.metrics.match_count), unit: '人' },
        { label: '配对参考', value: String(detail.metrics.pair_count), unit: '人' },
        { label: '占配对参考', value: ratioDisplay(exactValue(detail.metrics.share_pairs)) },
      ];
      break;
    case 'boundary_sensitivity':
      metrics = [
        { label: '低于边界', value: String(detail.metrics.strict_count), unit: '人' },
        { label: '包含边界', value: String(detail.metrics.inclusive_count), unit: '人' },
        { label: '边界新增', value: String(detail.metrics.added_count), unit: '人' },
      ];
      break;
  }
  const primary =
    detail.type === 'dimension_relation' || detail.type === 'rare_combination' ? 0 : 2;
  return (
    <>
      <dl className="focus-metrics" aria-label="发现关键指标">
        {metrics.map((metric, index) => (
          <div
            key={metric.label}
            className={index === primary ? 'focus-metric-primary' : undefined}
          >
            <dt>{metric.label}</dt>
            <dd>
              <strong>{metric.value}</strong>
              {metric.unit && <span>{metric.unit}</span>}
            </dd>
            {metric.note && <p>{metric.note}</p>}
          </div>
        ))}
      </dl>
      {detail.type === 'dimension_relation' && (
        <p className="focus-interpretation">相关不代表因果</p>
      )}
    </>
  );
}
export default function LiveDiscoveries({
  batch,
  onSelection,
  children,
  focusAction,
  onShowStatistics,
}: {
  batch: ApiBatch;
  onSelection?: (selection: DiscoverySelection, names?: { id: string; title: string }[]) => void;
  children: (focus: ReactNode) => ReactNode;
  focusAction?: ReactNode;
  onShowStatistics?: () => void;
}) {
  const { client, conditions, reportDraft, exploreDiscovery } = useDataMode();
  const router = useRouter();
  const contextKey = `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
  const checked =
    reportDraft?.key === contextKey ? (reportDraft.discovery?.discovery_ids ?? []) : [];
  const [view, setView] = useState('all');
  const [focusId, setFocusId] = useState('');
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
  const items = resource.data?.items ?? [];
  const focus = useMemo(
    () =>
      (resource.data?.items ?? [])
        .filter((card, index, all) => all.findIndex((other) => other.type === card.type) === index)
        .slice(0, 3),
    [resource.data],
  );
  const visible = useMemo(
    () => (resource.data?.items ?? []).filter((card) => view === 'all' || card.type === view),
    [resource.data, view],
  );
  const focused = focus.find((item) => item.discovery_id === focusId) ?? focus[0];
  const needed = focus;
  const loadDetails = useCallback(
    async (signal: AbortSignal) => {
      const result: Record<string, DiscoveryDetail> = {};
      // Sequential, bounded reads retain the backend's eligibility and concurrency checks.
      for (const card of needed)
        result[card.discovery_id] = await client.discoveryDetail(
          batch,
          card.discovery_id,
          spec,
          signal,
        );
      return result;
    },
    [client, batch, spec, needed],
  );
  const details = useRemote(
    needed.length && selected === null ? `discovery-card-details:${contextKey}:${offset}` : null,
    loadDetails,
  );
  const choose = (ids: string[]) => {
    if (!resource.data || !onSelection) return;
    const names = [
      ...new Map(
        [
          ...(reportDraft?.key === contextKey ? reportDraft.names : []),
          ...items.map((card) => ({ id: card.discovery_id, title: card.title })),
        ]
          .filter((item) => ids.includes(item.id))
          .map((item) => [item.id, item]),
      ).values(),
    ];
    onSelection({ input_key: resource.data.scope.input_key, discovery_ids: ids }, names);
  };
  const renderCards = (cards: typeof items) => (
    <div className="discovery-grid">
      {cards.map((card) => {
        const detail = details.data?.[card.discovery_id];
        const origin = detail ? originForDiscovery(detail, spec) : null;
        return (
          <section
            className="discovery-card"
            key={card.discovery_id}
            data-testid="discovery-card"
            data-discovery-id={card.discovery_id}
          >
            <div className="finding-top">
              <h3>{card.title}</h3>
              <Tag color="gold">{card.status === 'displayable' ? '待核查' : '需谨慎解释'}</Tag>
            </div>
            {detail ? (
              <FocusMetrics detail={detail} />
            ) : (
              <p role="status" className="discovery-coverage">
                {details.error ? '涉及人数暂不可用，请重试核查' : '正在核查涉及人数…'}
              </p>
            )}
            <details className="focus-explanation">
              <summary>发现说明</summary>
              {detail && <p>{coverage(detail)}</p>}
              <p>{discoverySummaryText(card.summary)}</p>
            </details>
            <div className="batch-actions focus-card-actions">
              <Button type="primary" onClick={() => setSelected(card.discovery_id)}>
                查看发现与依据
              </Button>
              <Button
                disabled={!origin}
                onClick={() => {
                  if (origin) {
                    exploreDiscovery(origin);
                    router.push(discoveryUrl(origin));
                  }
                }}
              >
                进一步探索
              </Button>
              {onSelection && (
                <Checkbox
                  aria-label={`加入报告 ${card.title}`}
                  checked={checked.includes(card.discovery_id)}
                  disabled={!checked.includes(card.discovery_id) && checked.length >= 3}
                  onChange={(e) =>
                    choose(
                      e.target.checked
                        ? [...checked, card.discovery_id]
                        : checked.filter((id) => id !== card.discovery_id),
                    )
                  }
                >
                  {checked.includes(card.discovery_id) ? '已加入待保存' : '加入报告'}
                </Checkbox>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
  const renderRows = (cards: typeof items) => (
    <ul className="discovery-list">
      {cards.map((card) => (
        <li
          className="discovery-row"
          key={card.discovery_id}
          data-testid="discovery-card"
          data-discovery-id={card.discovery_id}
        >
          <div>
            <h3>{card.title}</h3>
            <p className="muted small">{discoverySummaryText(card.summary)}</p>
          </div>
          <div className="batch-actions">
            <Button onClick={() => setSelected(card.discovery_id)}>查看发现与依据</Button>
            {onSelection && (
              <Checkbox
                aria-label={`加入报告 ${card.title}`}
                checked={checked.includes(card.discovery_id)}
                disabled={!checked.includes(card.discovery_id) && checked.length >= 3}
                onChange={(event) =>
                  choose(
                    event.target.checked
                      ? [...checked, card.discovery_id]
                      : checked.filter((id) => id !== card.discovery_id),
                  )
                }
              >
                {checked.includes(card.discovery_id) ? '已加入待保存' : '加入报告'}
              </Checkbox>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
  const status = (
    <>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {details.error && <RequestError error={details.error} retry={details.retry} />}
    </>
  );
  const focusPanel = (
    <Panel
      className="discoveries-panel"
      title="学情分析"
      action={focusAction ?? <Button onClick={resource.retry}>刷新发现</Button>}
    >
      {status}
      <div className="discovery-focus-toolbar">
        {resource.data && (
          <p data-testid="discovery-scope">
            {resource.data.scope.scope_record_count === resource.data.scope.batch_record_count ? (
              <>
                本次测评 <strong>{resource.data.scope.batch_record_count}</strong> 人
              </>
            ) : (
              <>
                当前范围 <strong>{resource.data.scope.scope_record_count}</strong> / 本次测评{' '}
                {resource.data.scope.batch_record_count} 人
              </>
            )}
          </p>
        )}
        {focusAction && (
          <Button type="text" icon={<ReloadOutlined aria-hidden="true" />} onClick={resource.retry}>
            刷新发现
          </Button>
        )}
      </div>
      {resource.data && (
        <>
          {focus.length > 1 && (
            <div className="discovery-focus-picker">
              <Select
                id="focus-discovery"
                aria-label="查看重点发现"
                value={focused?.discovery_id}
                onChange={setFocusId}
                options={focus.map((item) => ({
                  value: item.discovery_id,
                  label: typeNames[item.type],
                }))}
              />
            </div>
          )}
          {focused ? (
            renderCards([focused])
          ) : (
            <div className="discovery-empty">
              <Empty description="当前范围没有符合条件的发现" />
              <p className="muted small">
                未发现符合当前规则的现象，不代表没有学习问题。可先查看评分分布，再探索具体群体。
              </p>
              <div className="batch-actions">
                {onShowStatistics && <Button onClick={onShowStatistics}>查看评分统计</Button>}
                <Button onClick={() => router.push('/explorer')}>进入画像探索</Button>
              </div>
            </div>
          )}
          {focused && <p className="discovery-selection-note">当前页代表发现，非全量排序。</p>}
        </>
      )}
      {onSelection && checked.length > 0 && (
        <div className="discovery-report-actions has-selection" aria-label="待保存发现">
          <span className="small" role="status">
            已加入待保存 {checked.length} 项
          </span>
          <Button type="link" onClick={() => choose([])}>
            清空已选
          </Button>
          <Button type="primary" onClick={() => router.push('/reports')}>
            前往报告
          </Button>
        </div>
      )}
    </Panel>
  );
  return (
    <>
      {children(focusPanel)}
      <details className="panel all-discoveries-panel">
        <summary>全部发现和统计详情</summary>
        <div className="discovery-toolbar">
          <p>分析范围：{conditionText(spec.scope_conditions)}</p>
          <Select
            aria-label="查看发现类型"
            value={view}
            options={[
              { value: 'all', label: '全部发现' },
              ...Object.entries(typeNames).map(([value, label]) => ({ value, label })),
            ]}
            onChange={setView}
          />
        </div>
        {resource.data && (
          <>
            {view === 'all' ? (
              Object.entries(typeNames)
                .filter(([type]) => items.some((card) => card.type === type))
                .map(([type, label]) => (
                  <details className="discovery-type-group" key={type}>
                    <summary>
                      {label} · 当前页 {items.filter((card) => card.type === type).length} 项
                    </summary>
                    {renderRows(items.filter((card) => card.type === type))}
                  </details>
                ))
            ) : visible.length ? (
              renderRows(visible)
            ) : (
              <p>当前页暂无此类型发现，可查看其他页。</p>
            )}
            {resource.data.truncated && (
              <p className="muted small">
                本次保留 {resource.data.retained_count} 项结果，并非全部可能的发现。
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
      </details>
      {selected && resource.data && (
        <DiscoveryDetailDrawer
          key={selected}
          batch={batch}
          spec={spec}
          discoveryId={selected}
          onClose={() => setSelected(null)}
          reportSelection={
            onSelection
              ? {
                  checked: checked.includes(selected),
                  disabled: !checked.includes(selected) && checked.length >= 3,
                  onChange: (include) =>
                    choose(
                      include ? [...checked, selected] : checked.filter((id) => id !== selected),
                    ),
                }
              : undefined
          }
        />
      )}
    </>
  );
}
export function DiscoveryDetailDrawer({
  batch,
  spec,
  discoveryId,
  onClose,
  allowExplore = true,
  reportSelection,
}: {
  batch: ApiBatch;
  spec: DiscoverySpec;
  discoveryId: string;
  onClose: () => void;
  allowExplore?: boolean;
  reportSelection?: {
    checked: boolean;
    disabled: boolean;
    onChange: (include: boolean) => void;
  };
}) {
  const { client, exploreDiscovery } = useDataMode();
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
  return (
    <>
      <Drawer title="发现详情" open onClose={onClose} size="min(720px, 100vw)">
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
            {reportSelection && (
              <div className="discovery-detail-selection">
                <Checkbox
                  checked={reportSelection.checked}
                  disabled={reportSelection.disabled}
                  onChange={(event) => reportSelection.onChange(event.target.checked)}
                >
                  {reportSelection.checked ? '已加入待保存' : '加入报告'}
                </Checkbox>
                <span className="muted small">
                  {reportSelection.disabled
                    ? '已选满三项，可返回调整选择。'
                    : '加入后仍需在分析报告中保存和核验，不会自动确认为结论。'}
                </span>
              </div>
            )}
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
            <Button
              type="primary"
              disabled={!allowExplore}
              onClick={() => {
                const origin = originForDiscovery(d, spec);
                if (!origin) return;
                exploreDiscovery(origin);
                onClose();
                router.push(discoveryUrl(origin));
              }}
            >
              在画像探索查看原群体
            </Button>
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
      <Drawer title="发现群体与评分证据" open onClose={onClose} size="min(720px, 100vw)">
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
                        {scoreDisplay(score.score)} 分
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
