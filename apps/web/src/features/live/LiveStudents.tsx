'use client';
import { useCallback, useState } from 'react';
import { Button, Drawer, Empty, Pagination, Table } from 'antd';
import { LeftOutlined, RightOutlined } from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import {
  DIMENSIONS,
  type ApiBatch,
  type ApiEvidence,
  type ApiStudent,
  type InsightSpec,
} from '../../services/edu-api/types';
import {
  commentStatusText,
  conditionText,
  dimensionName,
  metricDisplay,
  scoreDisplay,
  studentScoreDisplay,
} from '../../services/edu-api/view-model';
import { AssessmentContext, Panel } from '../../components/ui';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';

export function LiveStudentTable({
  items,
  total,
  offset,
  onPage,
  onStudent,
  onEvidence,
  onProfile,
}: {
  items: ApiStudent[];
  total: number;
  offset: number;
  onPage: (offset: number) => void;
  onStudent: (student: ApiStudent) => void;
  onEvidence?: (evidenceId: string) => void;
  onProfile?: (student: ApiStudent) => void;
}) {
  return (
    <Table<ApiStudent>
      rowKey="student_id"
      dataSource={items}
      scroll={{ x: 750 }}
      size="small"
      locale={{ emptyText: '没有匹配记录，请调整筛选条件。' }}
      pagination={{
        current: Math.floor(offset / 10) + 1,
        total,
        pageSize: 10,
        showSizeChanger: false,
        onChange: (page) => onPage((page - 1) * 10),
        showTotal: (n) => `共 ${n} 名学生`,
      }}
      columns={[
        { title: '学生标识', dataIndex: 'display_label' },
        ...DIMENSIONS.map((d) => ({
          title: d.name,
          key: d.code,
          align: 'center' as const,
          render: (_: unknown, student: ApiStudent) => {
            const score = student.scores.find((s) => s.dimension_code === d.code);
            return score ? (
              onEvidence ? (
                <Button
                  className="score-evidence-link"
                  type="link"
                  onClick={() => onEvidence(score.evidence_id)}
                  aria-label={`${student.display_label} ${d.name} ${studentScoreDisplay(score)} 分，查看评分依据`}
                >
                  {studentScoreDisplay(score)}
                </Button>
              ) : (
                studentScoreDisplay(score)
              )
            ) : (
              <span className="muted">未纳入本指标核算</span>
            );
          },
        })),
        {
          title: '操作',
          key: 'detail',
          render: (_, student) => (
            <div className="student-row-actions">
              {onProfile && (
                <Button
                  type="link"
                  aria-label={`查看 ${student.display_label} 能力画像`}
                  onClick={() => onProfile(student)}
                >
                  能力画像
                </Button>
              )}
              <Button
                type="link"
                onClick={() => onStudent(student)}
                aria-label={`查看 ${student.display_label} 的测评详情`}
              >
                详情 →
              </Button>
            </div>
          ),
        },
      ]}
    />
  );
}
function EvidenceContent({ evidence }: { evidence: ApiEvidence }) {
  return (
    <Panel title={`${dimensionName(evidence.dimension_code)} · ${scoreDisplay(evidence.score)} 分`}>
      <p>原始数值：{evidence.score_raw}</p>
      <div className="withheld-note">{commentStatusText(evidence.comment_status)}</div>
      {evidence.comment_status === 'published' && (
        <>
          <p className="reviewed-text">{evidence.comment}</p>
          <p className="muted small">
            审核展示版本 {evidence.comment_review_version} · 未授予公开发布许可
          </p>
        </>
      )}
      <dl className="source-details">
        <dt>评分来源</dt>
        <dd>
          {evidence.source.sheet_name} · 第 {evidence.source.row_number} 行 ·{' '}
          {evidence.source.score_cell}
        </dd>
        <dt>维度 / 评语单元格</dt>
        <dd>
          {evidence.source.dimension_cell} / {evidence.source.comment_cell}
        </dd>
      </dl>
      <details className="technical-details">
        <summary>文件校验信息</summary>
        <p className="small">{evidence.source.file_sha256}</p>
      </details>
    </Panel>
  );
}
export function LiveScoreDrawer({
  batch,
  evidenceId,
  onClose,
}: {
  batch: ApiBatch;
  evidenceId: string | null;
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [evidence, rubric] = await Promise.all([
        client.evidence(batch, evidenceId!, signal),
        client.rubric(batch, signal),
      ]);
      return { evidence, rubric };
    },
    [client, batch, evidenceId],
  );
  const resource = useRemote(
    evidenceId ? `single-evidence:${batch.batch_id}:${batch.snapshot_id}:${evidenceId}` : null,
    load,
  );
  const criterion = resource.data?.rubric.criteria.find(
    (c) => c.rubric_criterion_id === resource.data?.evidence.rubric_criterion_id,
  );
  return (
    <Drawer title="测评评分依据" open={Boolean(evidenceId)} onClose={onClose} size={550}>
      <AssessmentContext title={batch.title} course={batch.course} date={batch.assessment_date} />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && (
        <>
          <EvidenceContent evidence={resource.data.evidence} />
          <Panel title="评分依据">
            <p>{criterion?.display_name ?? '未找到对应依据'}</p>
            {criterion?.criteria_status === 'published' ? (
              <>
                <div className="withheld-note">已审核评分依据 · 仅限授权教师</div>
                <p className="reviewed-text">{criterion.criteria_text}</p>
                <p className="muted small">
                  审核展示版本 {criterion.criteria_review_version} · 未授予公开发布许可
                </p>
              </>
            ) : (
              <div className="withheld-note">评分依据正文暂未开放（待审核）</div>
            )}
            {criterion && (
              <p className="source-line">
                {criterion.source.sheet_name} · {criterion.source.range}
              </p>
            )}
            <details className="technical-details">
              <summary>标准版本</summary>
              <p className="muted small">{resource.data.rubric.rubric_version}</p>
            </details>
          </Panel>
        </>
      )}
    </Drawer>
  );
}
export function LiveStudentDrawer({
  batch,
  student,
  pageItems = [],
  onStudent,
  onClose,
}: {
  batch: ApiBatch;
  student: ApiStudent | null;
  pageItems?: ApiStudent[];
  onStudent?: (student: ApiStudent) => void;
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const load = useCallback(
    (signal: AbortSignal) => client.studentEvidence(batch, student!.student_id, signal),
    [client, batch, student],
  );
  const resource = useRemote(
    student ? `student:${batch.batch_id}:${batch.snapshot_id}:${student.student_id}` : null,
    load,
  );
  const index = pageItems.findIndex((s) => s.student_id === student?.student_id);
  return (
    <Drawer
      title={
        <div className="student-drawer-heading">
          <span>学生标识 {student?.display_label}</span>
          {onStudent && (
            <div>
              <Button
                aria-label="当前页上一名学生"
                disabled={index <= 0}
                icon={<LeftOutlined />}
                onClick={() => {
                  const previous = pageItems[index - 1];
                  if (previous) onStudent(previous);
                }}
              />
              <Button
                aria-label="当前页下一名学生"
                disabled={index < 0 || index >= pageItems.length - 1}
                icon={<RightOutlined />}
                onClick={() => {
                  const next = pageItems[index + 1];
                  if (next) onStudent(next);
                }}
              />
            </div>
          )}
        </div>
      }
      open={Boolean(student)}
      onClose={onClose}
      size={560}
    >
      <AssessmentContext title={batch.title} course={batch.course} date={batch.assessment_date} />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data?.items.map((e) => (
        <EvidenceContent key={e.evidence_id} evidence={e} />
      ))}
    </Drawer>
  );
}
export interface MetricSelection {
  candidateId: string;
  metricKey: string;
  spec: InsightSpec;
  title: string;
}
export function MetricEvidenceDrawer({
  batch,
  selection,
  onClose,
}: {
  batch: ApiBatch;
  selection: MetricSelection | null;
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const [offset, setOffset] = useState(0);
  const [student, setStudent] = useState<ApiStudent | null>(null);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) =>
      client.records(
        batch,
        selection!.candidateId,
        selection!.metricKey,
        selection!.spec,
        offset,
        signal,
      ),
    [client, batch, selection, offset],
  );
  const resource = useRemote(
    selection ? `metric:${batch.batch_id}:${JSON.stringify(selection)}:${offset}` : null,
    load,
  );
  const result = resource.data;
  return (
    <>
      <Drawer open={Boolean(selection)} onClose={onClose} size={740} title="统计指标 · 评分依据">
        <p className="drawer-subtitle">{selection?.title}</p>
        {resource.loading && <LoadingPanel />}
        {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
        {result && (
          <>
            <Panel title="统计说明">
              <p>
                当前指标：<b>{metricDisplay(result.metric)}</b>
              </p>
              <p>
                指标来源记录数：{result.metric.source_record_count} · 参与记录总数：
                {result.total_count}
              </p>
              <p>
                统计分母：{result.metric.denominator ?? '不适用'} · 卡片范围人数：
                {result.scope_record_count}
              </p>
              <p className="muted small">人数按完整结果统计，翻页时保持一致。</p>
              <div className="condition-box">{conditionText(selection!.spec.conditions)}</div>
            </Panel>
            {result.total_count ? (
              <LiveStudentTable
                items={result.items}
                total={result.total_count}
                offset={offset}
                onPage={setOffset}
                onStudent={setStudent}
                onEvidence={setEvidenceId}
              />
            ) : (
              <Empty description="当前范围没有纳入该指标的评分记录。" />
            )}
            {!result.total_count && result.page.has_more && <Pagination current={1} total={0} />}
          </>
        )}
      </Drawer>
      <LiveStudentDrawer batch={batch} student={student} onClose={() => setStudent(null)} />
      <LiveScoreDrawer batch={batch} evidenceId={evidenceId} onClose={() => setEvidenceId(null)} />
    </>
  );
}
