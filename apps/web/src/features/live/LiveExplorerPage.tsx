'use client';
import { useCallback, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Input, Select, Tag } from 'antd';
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
} from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, NoBatch, RequestError } from './LiveStatus';
import { SparseMatrix } from './ServerCharts';
import { LiveScoreDrawer, LiveStudentDrawer, LiveStudentTable } from './LiveStudents';

interface Draft {
  dimension_code: DimensionCode;
  operator: ApiCondition['operator'];
  value: string;
}
export default function LiveExplorerPage() {
  const { batch } = useDataMode();
  return batch ? <Explorer batch={batch} /> : <NoBatch />;
}
function Explorer({ batch }: { batch: ApiBatch }) {
  const { client, conditions, setConditions } = useDataMode();
  const router = useRouter();
  const [draft, setDraft] = useState<Draft[]>(() =>
    conditions.map((c) => ({ ...c, value: String(c.value) })),
  );
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
      if (conditions.length) {
        const group = await client.group(batch, conditions, offset, signal);
        return {
          items: group.items,
          matched: group.matched_count,
          total: group.total_count,
          ratio: group.ratio,
          statistics: group.statistics,
        };
      }
      const page = await client.students(batch, offset, signal);
      return {
        items: page.items,
        matched: page.total_count,
        total: page.total_count,
        ratio: page.total_count ? 1 : 0,
        statistics: null,
      };
    },
    [client, batch, conditions, offset],
  );
  const group = useRemote(
    `group:${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}:${offset}`,
    loadGroup,
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
    setOffset(0);
    setStudent(null);
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
      setOffset(0);
      setValidation(null);
      setStudent(null);
      setEvidenceId(null);
    },
    [axes, setConditions],
  );
  const distribution = group.data?.statistics ?? (conditions.length ? null : stats.data);
  return (
    <div className="page-stack explorer-page live-explorer">
      <Panel className="filter-panel">
        <div className="filter-builder">
          {draft.map((condition, index) => (
            <div className="filter-condition" key={index}>
              {index > 0 && <span className="and-token">且</span>}
              <Select
                className="dimension-select"
                aria-label={`正式条件${index + 1}维度`}
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
                aria-label={`正式条件${index + 1}比较`}
                value={condition.operator}
                options={Object.entries(operatorLabels).map(([value, label]) => ({ value, label }))}
                onChange={(operator) =>
                  setDraft(draft.map((c, i) => (i === index ? { ...c, operator } : c)))
                }
              />
              <Input
                className="live-threshold"
                inputMode="decimal"
                aria-label={`正式条件${index + 1}评分`}
                value={condition.value}
                maxLength={32}
                onChange={(e) =>
                  setDraft(draft.map((c, i) => (i === index ? { ...c, value: e.target.value } : c)))
                }
                onPressEnter={apply}
              />
              <Button
                type="text"
                aria-label={`移除正式条件${index + 1}`}
                onClick={() => setDraft(draft.filter((_, i) => i !== index))}
              >
                ×
              </Button>
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
              setDraft([]);
              setConditions([]);
              setValidation(null);
              setOffset(0);
              setStudent(null);
              setEvidenceId(null);
            }}
          >
            重置
          </Button>
          <span className="muted small">至多两个不同维度 · AND · 精确小数阈值</span>
        </div>
        {validation && (
          <Alert className="session-notice" type="error" showIcon title={validation} />
        )}
      </Panel>
      <div className="match-summary">
        <span>
          当前匹配 <strong>{group.data?.matched ?? '—'}</strong> / {group.data?.total ?? '—'} 人 ·
          占本批次 <b>{group.data ? `${(group.data.ratio * 100).toFixed(2)}%` : '—'}</b>
        </span>
        <Tag>服务端完整结果，分页不改变人数</Tag>
        <p>{conditionText(conditions)}</p>
      </div>
      <div className="explorer-grid">
        <Panel title="能力组合分布" subtitle="全批次稀疏矩阵 · 实际评分组合">
          <div className="axis-controls">
            <Select
              aria-label="正式热力图横轴"
              value={axes.x}
              options={DIMENSIONS.map((d) => ({ value: d.code, label: `横轴 · ${d.name}` }))}
              onChange={(x) => setAxes({ x, y: x === axes.y ? axes.x : axes.y })}
            />
            <Select
              aria-label="正式热力图纵轴"
              value={axes.y}
              options={DIMENSIONS.filter((d) => d.code !== axes.x).map((d) => ({
                value: d.code,
                label: `纵轴 · ${d.name}`,
              }))}
              onChange={(y) => setAxes({ ...axes, y })}
            />
          </div>
          {stats.loading && <LoadingPanel />}
          {stats.error && <RequestError error={stats.error} retry={stats.retry} />}
          {stats.data && (
            <SparseMatrix statistics={stats.data} conditions={conditions} onCell={selectCell} />
          )}
        </Panel>
        <section className="finding-card group-summary-card">
          <div className="finding-top">
            <Tag>当前正式群体</Tag>
            <small>服务端统计</small>
          </div>
          <h2>{conditions.length ? '符合当前能力组合条件的学生' : '当前批次全部学生'}</h2>
          <p>{conditionText(conditions)}</p>
          <div className="finding-numbers">
            <strong>
              {group.data?.matched ?? '—'}
              <small> / {group.data?.total ?? '—'} 人</small>
            </strong>
            <strong>{group.data ? `${(group.data.ratio * 100).toFixed(2)}%` : '—'}</strong>
          </div>
          {group.error && <RequestError error={group.error} retry={group.retry} />}
          <div className="group-distribution">
            <h3>群体评分概况</h3>
            {distribution ? (
              distribution.dimensions.map((d) => (
                <div className="server-mean-row" key={d.dimension_code}>
                  <span>{dimensionName(d.dimension_code)}</span>
                  <b>{d.mean.display}</b>
                  <span className="muted small">有效 {d.valid_count} 人</span>
                </div>
              ))
            ) : (
              <p className="muted">
                {group.loading ? '正在读取群体统计…' : '当前群体暂无可显示的统计。'}
              </p>
            )}
            <p className="muted small">均值取自服务端全体匹配记录，空值不补成 0。</p>
          </div>
        </section>
      </div>
      <Panel
        title="匹配学生"
        subtitle={`完整结果 ${group.data?.matched ?? '—'} 人；每页 10 人，顺序不代表排名。`}
      >
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
              setEvidenceId(null);
            }}
            onStudent={setStudent}
            onEvidence={setEvidenceId}
          />
        )}
      </Panel>
      <section className="panel teaching-strip">
        <div>
          <h2>当前分析对象：{group.data?.matched ?? '—'} 人群体</h2>
          <p className="muted">按同一批次、快照与已应用条件查询系统洞察。</p>
        </div>
        <Button
          type="primary"
          disabled={!group.data?.matched}
          onClick={() => router.push('/insights')}
        >
          查看这组评分洞察
        </Button>
      </section>
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
