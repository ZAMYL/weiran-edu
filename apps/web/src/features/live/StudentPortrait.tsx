'use client';
import { useCallback, useState } from 'react';
import { Button, Checkbox, Empty, Table } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import {
  DIMENSIONS,
  type ApiBatch,
  type ApiStatistics,
  type ApiStudent,
} from '../../services/edu-api/types';
import { scoreDisplay } from '../../services/edu-api/view-model';
import { studentPortraitRows } from '../../services/edu-api/student-portrait';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';

type PortraitRow = ReturnType<typeof studentPortraitRows>[number];
const unavailable = {
  missing: '未提供有效评分',
  multiple: '多条评分，需核查',
  out_of_range: '超出评分范围',
  valid: '',
};
const point = (index: number, radius: number) => {
  const angle = (index * Math.PI * 2) / 5 - Math.PI / 2;
  return [250 + Math.cos(angle) * radius, 215 + Math.sin(angle) * radius];
};
function PortraitChart({
  rows,
  minimum,
  maximum,
  reference,
}: {
  rows: PortraitRow[];
  minimum: number;
  maximum: number;
  reference: (number | null)[] | null;
}) {
  const complete = rows.every((row) => row.score !== null);
  const baseline = reference?.every(
    (score) => score !== null && score >= minimum && score <= maximum,
  )
    ? (reference as number[])
    : null;
  const points = (values: number[]) =>
    values
      .map((value, index) =>
        point(index, (130 * (value - minimum)) / (maximum - minimum)).join(','),
      )
      .join(' ');
  if (!complete)
    return (
      <div
        className="student-score-bars"
        role="img"
        aria-label="不完整学生评分图；未提供评分的维度不补零"
      >
        {rows.map((row) => (
          <div key={row.code}>
            <span>{row.name}</span>
            <strong>{row.score === null ? '—' : scoreDisplay(row.score)}</strong>
            <div className={`student-score-track${row.score === null ? ' is-unavailable' : ''}`}>
              {row.score !== null && (
                <span
                  style={{ width: `${(100 * (row.score - minimum)) / (maximum - minimum)}%` }}
                />
              )}
            </div>
            {row.score === null && <p>{unavailable[row.status]}</p>}
          </div>
        ))}
        <p className="muted">评分不完整，暂不绘制完整雷达图。</p>
      </div>
    );
  return (
    <div className="student-radar-chart">
      <svg
        viewBox="0 0 500 430"
        role="img"
        aria-label={`学生能力雷达图：${rows.map((row) => `${row.name} ${scoreDisplay(row.score)} 分`).join('；')}`}
      >
        {[1, 2, 3, 4].map((step) => (
          <g key={step}>
            <polygon
              points={Array.from({ length: 5 }, (_, index) =>
                point(index, (130 * step) / 4).join(','),
              ).join(' ')}
              fill="none"
              stroke="#dce5f4"
            />
            <text x="255" y={215 - (130 * step) / 4 + 12} className="radar-tick">
              {scoreDisplay(minimum + ((maximum - minimum) * step) / 4)}
            </text>
          </g>
        ))}
        {rows.map((row, index) => {
          const end = point(index, 130),
            label = point(index, 168);
          const anchor = index === 0 ? 'middle' : index < 3 ? 'start' : 'end';
          return (
            <g key={row.code}>
              <line x1="250" y1="215" x2={end[0]} y2={end[1]} stroke="#dce5f4" />
              <text x={label[0]} y={label[1]} textAnchor={anchor} className="radar-axis-label">
                {row.name}
              </text>
              <text x={label[0]} y={label[1]} textAnchor={anchor} className="radar-axis-short">
                {['算法结构', '代码实现', '问题建模', '效率意识', '解题策略'][index]}
              </text>
              <text
                x={label[0]}
                y={label[1]! + 22}
                textAnchor={anchor}
                className="radar-score-label"
              >
                {scoreDisplay(row.score)}
              </text>
            </g>
          );
        })}
        {baseline && (
          <polygon
            points={points(baseline)}
            fill="none"
            stroke="#7b8da9"
            strokeWidth="2"
            strokeDasharray="5 4"
          />
        )}
        <polygon
          points={points(rows.map((row) => row.score!))}
          fill="#2463f526"
          stroke="#2463f5"
          strokeWidth="2.5"
        />
        {rows.map((row, index) => {
          const p = point(index, (130 * (row.score! - minimum)) / (maximum - minimum));
          return <circle key={row.code} cx={p[0]} cy={p[1]} r="4" fill="#2463f5" />;
        })}
      </svg>
      <div className="student-chart-legend">
        <span>
          <i />
          本人评分
        </span>
        {baseline && (
          <span>
            <i className="reference-line" />
            测评平均分
          </span>
        )}
      </div>
      {reference && !baseline && <p className="muted">测评均值不完整，图中仅显示本人评分。</p>}
    </div>
  );
}

export default function StudentPortrait({
  batch,
  student,
  reference,
  referenceError,
  retryReference,
  onEvidence,
  onRecords,
}: {
  batch: ApiBatch;
  student: ApiStudent | null;
  reference: ApiStatistics | null;
  referenceError: ApiError | null;
  retryReference: () => void;
  onEvidence: (id: string) => void;
  onRecords: (student: ApiStudent) => void;
}) {
  const { client } = useDataMode();
  const [compare, setCompare] = useState(true);
  const load = useCallback(
    async (signal: AbortSignal) => {
      const evidence = await client.studentEvidence(batch, student!.student_id, signal);
      if (
        evidence.student_id !== student!.student_id ||
        evidence.items.some(
          (item) =>
            item.student_id !== student!.student_id ||
            item.batch_id !== batch.batch_id ||
            item.snapshot_id !== batch.snapshot_id,
        )
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      const rubric = await client.rubric(batch, signal);
      if (!(rubric.score_policy.maximum > rubric.score_policy.minimum))
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return { evidence, rubric };
    },
    [client, batch, student],
  );
  const resource = useRemote(
    student
      ? `student-portrait:${batch.batch_id}:${batch.snapshot_id}:${student.student_id}`
      : null,
    load,
  );
  const rows = resource.data
    ? studentPortraitRows(resource.data.evidence.items, resource.data.rubric.score_policy)
    : [];
  const means =
    reference?.scope.kind === 'batch'
      ? DIMENSIONS.map(
          (dimension) =>
            reference.dimensions.find((d) => d.dimension_code === dimension.code)?.mean.value ??
            null,
        )
      : null;
  return (
    <Panel
      className="student-portrait-panel"
      title={student ? `${student.display_label} · 能力画像` : '学生能力画像'}
      action={
        student ? (
          <Button type="link" onClick={() => onRecords(student)}>
            查看原始记录
          </Button>
        ) : undefined
      }
    >
      {!student && <Empty description="选择学生查看能力画像" />}
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && student && (
        <>
          <div className="student-portrait-toolbar">
            <Checkbox checked={compare} onChange={(event) => setCompare(event.target.checked)}>
              对照测评平均分
            </Checkbox>
          </div>
          {compare && referenceError && (
            <RequestError error={referenceError} retry={retryReference} />
          )}
          {compare && !means && !referenceError && (
            <p className="muted" role="status">
              测评平均分暂不可用，当前显示本人评分。
            </p>
          )}
          <div
            className="student-portrait-content"
            data-testid="student-portrait-content"
            data-student-id={student.student_id}
          >
            <PortraitChart
              rows={rows}
              minimum={resource.data.rubric.score_policy.minimum}
              maximum={resource.data.rubric.score_policy.maximum}
              reference={compare ? means : null}
            />
            <div className="student-portrait-scores">
              <Table<PortraitRow>
                rowKey="code"
                size="small"
                dataSource={rows}
                pagination={false}
                columns={[
                  { title: '能力维度', dataIndex: 'name' },
                  {
                    title: '本人评分',
                    render: (_, row) =>
                      row.score === null ? unavailable[row.status] : scoreDisplay(row.score),
                  },
                  ...(compare
                    ? [
                        {
                          title: '测评平均',
                          render: (_: unknown, row: PortraitRow) =>
                            scoreDisplay(
                              means?.[DIMENSIONS.findIndex((d) => d.code === row.code)] ?? null,
                            ),
                        },
                      ]
                    : []),
                  {
                    title: '评分依据',
                    render: (_, row) =>
                      row.evidence.length
                        ? row.evidence.map((evidence, index) => (
                            <Button
                              key={evidence.evidence_id}
                              type="link"
                              aria-label={`${row.name} 个人评分依据${row.evidence.length > 1 ? ` ${index + 1}` : ''}`}
                              onClick={() => onEvidence(evidence.evidence_id)}
                            >
                              {row.evidence.length > 1 ? `记录 ${index + 1}` : '查看'}
                            </Button>
                          ))
                        : '—',
                  },
                ]}
              />
              <details className="student-portrait-note">
                <summary>画像说明</summary>
                <p>
                  本次测评的有效评分，范围 {resource.data.rubric.score_policy.minimum}–
                  {resource.data.rubric.score_policy.maximum}{' '}
                  分。参照来自本次测评全部参评者的各维度有效评分均值；不是当前筛选群体的均值。
                </p>
                <p>
                  评分反映本次测评表现，不代表稳定能力或排名。未提供唯一有效评分的维度不补零、不合并计算。
                </p>
              </details>
            </div>
          </div>
        </>
      )}
    </Panel>
  );
}
