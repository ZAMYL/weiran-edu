'use client';
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { Checkbox, Button, Drawer, Empty, Pagination, Select, Table } from 'antd';
import { useRouter } from 'next/navigation';
import { ReloadOutlined } from '@ant-design/icons';
import type { DiscoverySelection } from '../../services/edu-api/analysis-types';
import { discoveryUrl, originForDiscovery } from '../../services/edu-api/discovery-navigation';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import { describeDiscovery } from '../../services/edu-api/teacher-analysis';
import { discoverySummaryText } from '../../services/edu-api/discovery-view';
import {
  discoveryConditionsText,
  discoveryEvidenceNotes,
  discoveryPresentation,
  eligibleForFocus,
} from '../../services/edu-api/discovery-presentation';
import type { DiscoveryDetail, DiscoverySpec } from '../../services/edu-api/discovery-types';
import type { ApiBatch, ApiStudent } from '../../services/edu-api/types';
import {
  conditionText,
  dimensionName,
  scoreDisplay,
  ratioDisplay,
} from '../../services/edu-api/view-model';
import { DiscoveryChart, exactValue, scoreText } from './DiscoveryChart';
import { LiveScoreDrawer } from './LiveStudents';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';

const typeNames = {
  dimension_relation: '两项评分的关系',
  group_difference: '两组学生的评分差别',
  rare_combination: '少见评分组合',
  boundary_sensitivity: '边界比较',
};
const metricNames: Record<string, string> = {
  paired_scores: '配对评分学生',
  target_members: '符合条件的学生',
  remainder_members: '其他可判定学生',
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
    ? `${side === 'target' ? '符合条件的学生' : '其他可判定学生'} · ${dimensionName(codes[dim])}`
    : '查看该指标评分依据';
}
function metricsText(d: DiscoveryDetail) {
  switch (d.type) {
    case 'dimension_relation':
      return `配对学生 ${d.metrics.paired_count} 人；分析范围 ${d.metrics.student_denominator} 人；关联系数 ${d.metrics.rho ? scoreDisplay(d.metrics.rho.value) : '无法计算'}。`;
    case 'group_difference':
      return `符合条件的学生 ${d.metrics.target_count} 人，其他可判定学生 ${d.metrics.remainder_count} 人；分析范围 ${d.metrics.student_denominator} 人。比较指标：${dimensionName(d.metrics.outcome_dimension)}。`;
    case 'rare_combination':
      return `找到 ${d.metrics.match_count} 名学生；完整配对参考范围 ${d.metrics.pair_count} 人，占比 ${ratioDisplay(exactValue(d.metrics.share_pairs))}。`;
    case 'boundary_sensitivity':
      return `低于边界 ${d.metrics.strict_count} 人，包含边界 ${d.metrics.inclusive_count} 人，新增 ${d.metrics.added_count} 人；分析范围 ${d.metrics.student_denominator} 人。`;
  }
}
function FocusMetrics({ detail }: { detail: DiscoveryDetail }) {
  const presentation = discoveryPresentation(detail);
  return (
    <dl className="focus-metrics" aria-label="发现关键指标">
      {presentation.metrics.map((metric, index) => (
        <div
          key={metric.label}
          className={index === presentation.primary ? 'focus-metric-primary' : undefined}
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
  const [visibleCount, setVisibleCount] = useState(10);
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
    (signal: AbortSignal) => client.discoveries(batch, spec, 0, signal, 50),
    [client, batch, spec],
  );
  const resource = useRemote(
    `discovery-overview:${batch.batch_id}:${JSON.stringify(spec)}`,
    load,
    client.overviewCache,
  );
  const items = useMemo(() => resource.data?.items ?? [], [resource.data]);
  const candidatesKey = JSON.stringify(
    items.slice(0, 10)
      .filter((card) => card.status !== 'insufficient_evidence')
      .map((card) => ({ id: card.discovery_id, type: card.type })),
  );
  const visible = useMemo(
    () => items.filter((card) => view === 'all' || card.type === view),
    [items, view],
  );
  const loadDetails = useCallback(
    async (signal: AbortSignal) => {
      const result: Record<string, DiscoveryDetail> = {};
      // Sequential, bounded reads retain the backend's eligibility and concurrency checks.
      const represented = new Set<DiscoveryDetail['type']>();
      const candidates: { id: string; type: DiscoveryDetail['type'] }[] = JSON.parse(candidatesKey);
      for (const card of candidates) {
        if (represented.has(card.type)) continue;
        const detail = await client.discoveryDetail(batch, card.id, spec, signal);
        result[card.id] = detail;
        if (eligibleForFocus(detail)) represented.add(detail.type);
        if (represented.size >= 3) break;
      }
      return result;
    },
    [client, batch, spec, candidatesKey],
  );
  const details = useRemote(
    candidatesKey !== '[]' && !resource.error
      ? `discovery-card-details:${contextKey}:${candidatesKey}`
      : null,
    loadDetails,
    client.overviewCache,
  );
  const focus = useMemo(
    () =>
      items
        .filter((card) => {
          const detail = details.data?.[card.discovery_id];
          return detail && eligibleForFocus(detail);
        })
        .filter((card, index, all) => all.findIndex((other) => other.type === card.type) === index)
        .slice(0, 3),
    [items, details.data],
  );
  const titleFor = (card: (typeof items)[number]) => {
    const detail = details.data?.[card.discovery_id];
    return detail ? discoveryPresentation(detail).title : card.title;
  };
  const choose = (ids: string[]) => {
    if (!resource.data || !onSelection) return;
    const names = [
      ...new Map(
        [
          ...(reportDraft?.key === contextKey ? reportDraft.names : []),
          ...items.map((card) => ({ id: card.discovery_id, title: titleFor(card) })),
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
        const copy = detail ? describeDiscovery(detail) : null;
        const origin = detail ? originForDiscovery(detail, spec) : null;
        return (
          <section
            className="discovery-card"
            key={card.discovery_id}
            data-testid="discovery-card"
            data-discovery-id={card.discovery_id}
          >
            <div className="finding-top">
              <h3>{titleFor(card)}</h3>
            </div>
            {copy && (
              <p className="teacher-reading">
                {copy.numbers} {detail?.type === 'group_difference' && copy.whyItMatters}
              </p>
            )}
            {detail ? (
              <FocusMetrics detail={detail} />
            ) : (
              <p role="status" className="discovery-coverage">
                {details.error ? '涉及人数暂不可用，请重试核查' : '正在核查涉及人数…'}
              </p>
            )}
            <details className="focus-explanation">
              <summary>如何理解这条发现</summary>
              {copy && (
                <p>
                  {detail?.type !== 'group_difference' && copy.whyItMatters} {copy.limitation}
                </p>
              )}
            </details>
            <div className="batch-actions focus-card-actions">
              <Button type="primary" onClick={() => setSelected(card.discovery_id)}>
                查看具体评分
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
                看看这组学生
              </Button>
              {onSelection && (
                <Checkbox
                  aria-label={`加入报告 ${titleFor(card)}`}
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
            <h3>{titleFor(card)}</h3>
            <p className="teacher-reading">
              {details.data?.[card.discovery_id]
                ? describeDiscovery(details.data[card.discovery_id]!).numbers
                : discoverySummaryText(card.summary)
                    .replace(/目标组(\d+)人，余组(\d+)人/g, '本组 $1 人，其他学生 $2 人')
                    .replace('有效均值差为', '平均分相差 ')
                    .replace('（目标减余组）', '（本组减其他学生）')}
            </p>
            {(card.status === 'insufficient_evidence' ||
              (details.data?.[card.discovery_id] &&
                !eligibleForFocus(details.data[card.discovery_id]!))) && (
              <p className="muted small">样本或评分依据不足，仅供查看</p>
            )}
          </div>
          <div className="batch-actions">
            <Button onClick={() => setSelected(card.discovery_id)}>查看具体评分</Button>
            {onSelection && (
              <Checkbox
                aria-label={`加入报告 ${titleFor(card)}`}
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
          {details.loading ? (
            <p role="status">正在读取发现依据…</p>
          ) : focus.length ? (
            renderCards(focus)
          ) : (
            !details.error && (
              <div className="discovery-empty">
                <Empty
                  description={
                    items.length ? '当前发现的依据不足以重点展示' : '当前范围没有符合条件的发现'
                  }
                />
                <p className="muted small">
                  {items.length
                    ? '可在下方全部发现中核查样本和评分依据。'
                    : '未发现符合当前规则的现象，不代表没有学习问题。可先查看评分分布，再探索具体群体。'}
                </p>
                <div className="batch-actions">
                  {onShowStatistics && <Button onClick={onShowStatistics}>查看评分统计</Button>}
                  <Button onClick={() => router.push('/explorer')}>进入画像探索</Button>
                </div>
              </div>
            )
          )}
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
        <summary>更多评分线索</summary>
        <div className="discovery-toolbar">
          <p>分析范围：{conditionText(spec.scope_conditions)}</p>
          <Select
            aria-label="查看发现类型"
            value={view}
            options={[
              { value: 'all', label: '所有类型' },
              ...Object.entries(typeNames).map(([value, label]) => ({ value, label })),
            ]}
            onChange={(next) => {
              setView(next);
              setVisibleCount(10);
            }}
          />
        </div>
        {resource.data && (
          <>
            {visible.length ? (
              renderRows(visible.slice(0, visibleCount))
            ) : (
              <p>当前范围没有这类评分线索。</p>
            )}
            {resource.data.truncated && (
              <p className="muted small">
                这里展示本次分析保留的线索。
              </p>
            )}
            {visible.length > visibleCount && (
              <Button onClick={() => setVisibleCount((count) => count + 10)}>查看更多线索</Button>
            )}
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
  const copy = d ? describeDiscovery(d) : null;
  const explorerOrigin = d && allowExplore ? originForDiscovery(d, spec) : null;
  return (
    <>
      <Drawer title="发现详情" open onClose={onClose} size="min(720px, 100vw)">
        {resource.loading && <LoadingPanel />}
        {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
        {d && resource.data && copy && (
          <>
            <h2>{discoveryPresentation(d).title}</h2>
            <p>分析范围：{conditionText(spec.scope_conditions)}</p>
            {d.conditions.length > 0 && (
              <p>分组或筛选条件：{discoveryConditionsText(d.conditions)}</p>
            )}
            <p data-testid="discovery-metrics">{metricsText(d)}</p>
            <p className="teacher-reading">{copy.numbers}</p>
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
                  { title: '符合条件学生均值', render: (_, row) => scoreText(row.target.mean) },
                  {
                    title: '其他可判定学生均值',
                    render: (_, row) => scoreText(row.remainder.mean),
                  },
                  { title: '均值差', render: (_, row) => scoreText(row.difference) },
                ]}
              />
            )}
            <Button
              type="primary"
              disabled={!explorerOrigin}
              onClick={() => {
                if (!explorerOrigin) return;
                exploreDiscovery(explorerOrigin);
                onClose();
                router.push(discoveryUrl(explorerOrigin));
              }}
            >
              看看这组学生
            </Button>
            <h3>查看学生与评分依据</h3>
            <div className="live-metrics">
              {d.controlled_queries.map((q) => (
                <Button key={q.metric_key} onClick={() => setMetric(q.metric_key)}>
                  {sourceName(q.metric_key)}
                </Button>
              ))}
            </div>
            <details className="discovery-evidence-notes" aria-label="样本与使用限制">
              <summary>样本与使用限制</summary>
              {discoveryEvidenceNotes(d).map((note) => (
                <p key={note}>{note}</p>
              ))}
            </details>
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
      <Drawer title="发现群体与评分依据" open onClose={onClose} size="min(720px, 100vw)">
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
                        aria-label={`${student.display_label} ${dimensionName(dimension)} 评分依据`}
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
