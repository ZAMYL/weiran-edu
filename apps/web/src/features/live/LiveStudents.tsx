'use client';
import { useCallback, useState } from 'react';
import { Button, Drawer, Empty, Pagination, Table, Tag } from 'antd';
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
} from '../../services/edu-api/view-model';
import { Panel } from '../../components/ui';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';

export function LiveStudentTable({
  items,
  total,
  offset,
  onPage,
  onStudent,
  onEvidence,
}: {
  items: ApiStudent[];
  total: number;
  offset: number;
  onPage: (offset: number) => void;
  onStudent: (student: ApiStudent) => void;
  onEvidence?: (evidenceId: string) => void;
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
        { title: '匿名学生', dataIndex: 'display_label' },
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
                  aria-label={`${student.display_label} ${d.name} ${score.score} 分证据`}
                >
                  {score.score}
                </Button>
              ) : (
                score.score
              )
            ) : (
              <span className="muted">未作为本指标证据</span>
            );
          },
        })),
        {
          title: '操作',
          key: 'detail',
          render: (_, student) => (
            <Button
              type="link"
              onClick={() => onStudent(student)}
              aria-label={`查看 ${student.display_label} 正式详情`}
            >
              详情 →
            </Button>
          ),
        },
      ]}
    />
  );
}
function EvidenceContent({ evidence }: { evidence: ApiEvidence }) {
  return (
    <Panel title={`${dimensionName(evidence.dimension_code)} · ${evidence.score} 分`}>
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
        <dt>文件摘要</dt>
        <dd>{evidence.source.file_sha256}</dd>
      </dl>
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
    <Drawer title="服务端评分证据" open={Boolean(evidenceId)} onClose={onClose} size={550}>
      <p className="drawer-subtitle">{batch.title} · 同一批次与快照</p>
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
            <p className="muted small">标准版本：{resource.data.rubric.rubric_version}</p>
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
          <span>匿名学生 {student?.display_label}</span>
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
      <p className="drawer-subtitle">
        {batch.title} · {onStudent ? '前后切换仅限当前服务端页' : '授权评分记录'}
      </p>
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
      <Drawer open={Boolean(selection)} onClose={onClose} size={740} title="洞察指标 · 评分依据">
        <p className="drawer-subtitle">{selection?.title}</p>
        {resource.loading && <LoadingPanel />}
        {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
        {result && (
          <>
            <Panel title="指标口径" action={<Tag color="blue">服务端计算</Tag>}>
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
              <p className="muted small">
                此处人数来自完整结果，分页不改变统计口径；数值的有理数分母不代表学生人数。
              </p>
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
              <Empty description="该指标没有参与评分记录，未使用其他学生填充。" />
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
