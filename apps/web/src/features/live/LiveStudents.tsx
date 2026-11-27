'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Flex,
  Pagination,
  Space,
  Table,
  Typography,
} from 'antd';
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
      scroll={{ x: 'max-content' }}
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
            <Space size={tokens.sizeXXS} wrap className="student-row-actions">
              {onProfile && (
                <Button
                  type="link"
                  size="small"
                  aria-label={`查看 ${student.display_label} 能力画像`}
                  onClick={() => onProfile(student)}
                >
                  能力画像
                </Button>
              )}
              <Button
                type="link"
                size="small"
                onClick={() => onStudent(student)}
                aria-label={`查看 ${student.display_label} 的测评详情`}
              >
                详情 →
              </Button>
            </Space>
          ),
        },
      ]}
    />
  );
}
function EvidenceContent({ evidence }: { evidence: ApiEvidence }) {
  return (
    <Panel title={`${dimensionName(evidence.dimension_code)} · ${scoreDisplay(evidence.score)} 分`}>
      <Flex vertical gap={tokens.sizeSM}>
        <Typography.Text>原始数值：{evidence.score_raw}</Typography.Text>
        <Alert type="info" showIcon title={commentStatusText(evidence.comment_status)} />
        {evidence.comment_status === 'published' && (
          <Flex vertical gap={tokens.sizeXXS}>
            <Typography.Paragraph className="reviewed-text" style={{ margin: 0 }}>
              {evidence.comment}
            </Typography.Paragraph>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              审核展示版本 {evidence.comment_review_version} · 未授予公开发布许可
            </Typography.Text>
          </Flex>
        )}
        <Descriptions
          className="source-details"
          size="small"
          column={1}
          bordered
          items={[
            {
              key: 'source',
              label: '评分来源',
              children: `${evidence.source.sheet_name} · 第 ${evidence.source.row_number} 行 · ${evidence.source.score_cell}`,
            },
            {
              key: 'cells',
              label: '维度 / 评语单元格',
              children: `${evidence.source.dimension_cell} / ${evidence.source.comment_cell}`,
            },
          ]}
        />
        <Disclosure className="technical-details" label={<>文件校验信息</>}>
          <Typography.Text type="secondary" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>
            {evidence.source.file_sha256}
          </Typography.Text>
        </Disclosure>
      </Flex>
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
        <Flex vertical gap={tokens.size}>
          <EvidenceContent evidence={resource.data.evidence} />
          <Panel title="评分依据">
            <Flex vertical gap={tokens.sizeSM}>
              <Typography.Text>{criterion?.display_name ?? '未找到对应依据'}</Typography.Text>
              {criterion?.criteria_status === 'published' ? (
                <>
                  <Alert type="info" showIcon title="已审核评分依据 · 仅限授权教师" />
                  <Typography.Paragraph className="reviewed-text" style={{ margin: 0 }}>
                    {criterion.criteria_text}
                  </Typography.Paragraph>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    审核展示版本 {criterion.criteria_review_version} · 未授予公开发布许可
                  </Typography.Text>
                </>
              ) : (
                <Alert type="warning" showIcon title="评分依据正文暂未开放（待审核）" />
              )}
              {criterion && (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {criterion.source.sheet_name} · {criterion.source.range}
                </Typography.Text>
              )}
              <Disclosure className="technical-details" label={<>标准版本</>}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {resource.data.rubric.rubric_version}
                </Typography.Text>
              </Disclosure>
            </Flex>
          </Panel>
        </Flex>
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
          <Flex vertical gap={tokens.size}>
            <Panel title="统计说明">
              <Flex vertical gap={tokens.sizeSM}>
                <Descriptions
                  size="small"
                  column={1}
                  items={[
                    {
                      key: 'metric',
                      label: '当前指标',
                      children: <strong>{metricDisplay(result.metric)}</strong>,
                    },
                    {
                      key: 'records',
                      label: '指标来源记录 / 参与总数',
                      children: `${result.metric.source_record_count} / ${result.total_count}`,
                    },
                    {
                      key: 'scope',
                      label: '统计分母 / 卡片范围',
                      children: `${result.metric.denominator ?? '不适用'} / ${result.scope_record_count} 人`,
                    },
                  ]}
                />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  人数按完整结果统计，翻页时保持一致。
                </Typography.Text>
                <Alert
                  type="info"
                  showIcon
                  className="condition-box"
                  title={conditionText(selection!.spec.conditions)}
                />
              </Flex>
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
          </Flex>
        )}
      </Drawer>
      <LiveStudentDrawer batch={batch} student={student} onClose={() => setStudent(null)} />
      <LiveScoreDrawer batch={batch} evidenceId={evidenceId} onClose={() => setEvidenceId(null)} />
    </>
  );
}
