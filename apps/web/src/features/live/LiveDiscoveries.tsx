'use client';
import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { Button, Drawer, Empty, Pagination, Select, Table } from 'antd';
import { useRouter } from 'next/navigation';
import { discoveryUrl, originForDiscovery } from '../../services/edu-api/discovery-navigation';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { describeDiscovery } from '../../services/edu-api/teacher-analysis';
import { discoverySummaryText } from '../../services/edu-api/discovery-view';
import {
  discoveryEvidenceNotes,
  discoveryPresentation,
  eligibleForFocus,
} from '../../services/edu-api/discovery-presentation';
import type { DiscoveryDetail, DiscoverySpec } from '../../services/edu-api/discovery-types';
import type { ApiBatch, ApiStudent } from '../../services/edu-api/types';
import {
  conditionText,
  dimensionName,
  studentScoreDisplay,
} from '../../services/edu-api/view-model';
import { DiscoveryChart, scoreText } from './DiscoveryChart';
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
  children,
  focusAction,
  onShowStatistics,
}: {
  batch: ApiBatch;
  children: (focus: ReactNode) => ReactNode;
  focusAction?: ReactNode;
  onShowStatistics?: () => void;
}) {
  const { client, conditions, exploreDiscovery } = useDataMode();
  const router = useRouter();
  const contextKey = `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
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
    (signal: AbortSignal) => client.diagnosisDiscoveries(batch, spec, 0, signal, 50),
    [client, batch, spec],
  );
  const resource = useRemote(
    `discovery-overview:${batch.batch_id}:${JSON.stringify(spec)}`,
    load,
    client.overviewCache,
  );
  const items = useMemo(() => resource.data?.items ?? [], [resource.data]);
  const candidatesKey = JSON.stringify(
    items
      .slice(0, 10)
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
        const detail = await client.diagnosisDiscoveryDetail(batch, card.id, spec, signal);
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
    resource.refreshing,
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
  const renderCards = (cards: typeof items) => (
    <div className="discovery-grid">
      {cards.map((card) => {
        const detail = details.data?.[card.discovery_id];
        const copy = detail ? describeDiscovery(detail) : null;
        const why = detail?.why || card.why;
        const affected = detail?.affected ?? card.affected;
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
            {detail ? (
              <FocusMetrics detail={detail} />
            ) : (
              <p role="status" className="discovery-coverage">
                {affected && card.type !== 'rare_combination'
                  ? `涉及 ${affected.count} 人 · 分析范围 ${affected.total} 人`
                  : details.error
                    ? '涉及人数暂不可用，请重试核查'
                    : '正在核查涉及人数…'}
              </p>
            )}
            <details className="focus-explanation">
              <summary>如何理解这条发现</summary>
              {(copy || why) && (
                <p className="teacher-reading">
                  {why || copy?.numbers} {copy?.whyItMatters} {copy?.limitation}
                </p>
              )}
            </details>
            <div className="focus-card-actions">
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
    <Panel className="discoveries-panel" title="学情分析" action={focusAction}>
      {status}
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
                    ? '可在「更多评分线索」中查看样本和评分依据。'
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
    </Panel>
  );
  return (
    <>
      {children(focusPanel)}
      <details className="panel all-discoveries-panel" open>
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
            {resource.data.truncated && <p className="muted small">这里展示本次分析保留的线索。</p>}
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
}: {
  batch: ApiBatch;
  spec: DiscoverySpec;
  discoveryId: string;
  onClose: () => void;
  allowExplore?: boolean;
}) {
  const { client, exploreDiscovery } = useDataMode();
  const router = useRouter();
  const [metric, setMetric] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) => client.discoveryDetail(batch, discoveryId, spec, signal),
    [client, batch, discoveryId, spec],
  );
  const resource = useRemote(`discovery-detail:${discoveryId}:${JSON.stringify(spec)}`, load);
  const d = resource.data;
  const copy = d ? describeDiscovery(d) : null;
  const explorerOrigin = d && allowExplore ? originForDiscovery(d, spec) : null;
  return (
    <>
      <Drawer title="发现详情" open onClose={onClose} size="min(720px, 100vw)">
        {resource.loading && <LoadingPanel />}
        {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
        {d && copy && (
          <>
            <h2>{discoveryPresentation(d).title}</h2>
            <p>分析范围：{conditionText(spec.scope_conditions)}</p>
            <p className="teacher-reading">{copy.numbers}</p>
            <h3>查看学生与评分依据</h3>
            <div className="batch-actions">
              {d.controlled_queries.map((q, index) => (
                <Button
                  key={q.metric_key}
                  type={index === 0 ? 'primary' : 'default'}
                  onClick={() => setMetric(q.metric_key)}
                >
                  {sourceName(q.metric_key)}
                </Button>
              ))}
              {explorerOrigin && (
                <Button
                  onClick={() => {
                    exploreDiscovery(explorerOrigin);
                    onClose();
                    router.push(discoveryUrl(explorerOrigin));
                  }}
                >
                  看看这组学生
                </Button>
              )}
            </div>
            <DiscoveryChart data={d} />
            {d.type === 'group_difference' && (
              <details className="discovery-evidence-notes">
                <summary>详细对照数据</summary>
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
              </details>
            )}
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
                { title: '学生标识', dataIndex: 'display_label' },
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
                        {studentScoreDisplay(score)} 分
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
