'use client';

import { useCallback, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Flex,
  Input,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { ExperimentOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons';
import { tokens } from '../../app/theme';
import { useDataMode } from '../../app/data-mode';
import { Disclosure } from '../../components/Disclosure';
import {
  phenomenonLabel,
  programmingTaskStateLabels,
  type ProgrammingEvidence,
  type ProgrammingFinding,
  type ProgrammingStudentFocus,
  type ProgrammingStudentRef,
  type ProgrammingTask,
} from '../../services/edu-api/programming-ai-types';
import type {
  answerSheetEvidenceDto,
  answerSheetPatternsDto,
  answerSheetProblemStudentsDto,
} from '../../services/edu-api/answer-sheets';
import type { Decoded } from '../../services/edu-api/schema';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';
import type { useProgrammingAiTask } from './useProgrammingAiTask';

export interface StudentDrillFilter {
  problemLabel?: string;
  status?: string | null;
  testIndex?: number | null;
}

export type AnswerSheetPattern = Decoded<typeof answerSheetPatternsDto>['items'][number];
export type AnswerSheetStudentRow = ProgrammingStudentRef & {
  started_at?: string | null;
  finished_at?: string | null;
};
type AiHookState = ReturnType<typeof useProgrammingAiTask>;

function evidenceLocationText(item: ProgrammingEvidence) {
  const parts = [
    item.test_index != null ? `第 ${item.test_index} 点` : null,
    item.source_row != null ? `原表第 ${item.source_row} 行` : null,
    item.source_index != null ? `题块 ${item.source_index}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : '整题记录';
}

function executionOriginText(origin: ProgrammingTask['execution_origin']) {
  if (origin === 'qwen_live') return '真实模型（Qwen）';
  if (origin === 'http_mock') return '离线受控执行（未调用付费模型）';
  return '未执行';
}

function formatTestTag(testIndex: number | null) {
  return testIndex != null ? `测试点 ${testIndex}` : '整题判题';
}

function verdictTagColor(status: string) {
  if (/正确|通过|accepted/i.test(status)) return 'success';
  if (/部分|partial/i.test(status)) return 'warning';
  if (/错|超时|超限|异常|失败|wrong|timeout|error|limit/i.test(status)) return 'error';
  return 'default';
}

function ProgrammingAiActionButton({
  ai,
  onRefreshAssessment,
}: {
  ai: AiHookState;
  onRefreshAssessment: () => void;
}) {
  if (ai.canStart) {
    return (
      <Button
        type="primary"
        icon={<ExperimentOutlined aria-hidden="true" />}
        loading={ai.busy}
        disabled={ai.busy}
        onClick={ai.startInvestigation}
      >
        发起编程调查
      </Button>
    );
  }
  if (ai.running && ai.task) {
    return (
      <Button
        icon={<StopOutlined aria-hidden="true" />}
        loading={ai.busy}
        disabled={ai.busy}
        onClick={ai.cancelInvestigation}
      >
        停止调查
      </Button>
    );
  }
  if (ai.errorGuidance?.refreshAssessment) {
    return (
      <Button icon={<ReloadOutlined aria-hidden="true" />} onClick={onRefreshAssessment}>
        刷新测评
      </Button>
    );
  }
  return null;
}

function ProgrammingAiStatusBanner({
  ai,
  sourceSnapshotId,
  onRefreshAssessment,
}: {
  ai: AiHookState;
  sourceSnapshotId: string | null;
  onRefreshAssessment: () => void;
}) {
  if (ai.restoring) {
    return (
      <Space size="small" role="status">
        <Spin size="small" />
        <Typography.Text type="secondary">正在读取上次编程调查状态…</Typography.Text>
      </Space>
    );
  }

  if (!sourceSnapshotId) {
    return (
      <Alert
        type="warning"
        showIcon
        title="当前测评缺少来源快照，暂无法发起编程调查"
        description="请先确认测评资料来源已完成解析并保留原件。"
      />
    );
  }

  if (ai.running && ai.task && !ai.error) {
    return (
      <Flex
        align="center"
        justify="space-between"
        wrap
        gap={tokens.sizeSM}
        className="ai-task-actions ai-task-running"
      >
        <Space className="ai-task-status" role="status" size="small">
          <Spin size="small" className="ai-analysis-indicator" aria-hidden="true" />
          <span>{programmingTaskStateLabels[ai.task.state]}</span>
        </Space>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          核查工具 {ai.task.tool_attempts} 次 · 模型请求 {ai.task.model_requests} 次
        </Typography.Text>
      </Flex>
    );
  }

  if (ai.error) {
    if (ai.errorGuidance?.refreshAssessment) {
      return (
        <Alert
          type="error"
          showIcon
          title={ai.errorGuidance.title}
          description={ai.errorGuidance.detail}
          action={<Button onClick={onRefreshAssessment}>刷新测评</Button>}
        />
      );
    }
    return (
      <RequestError
        error={ai.error}
        retry={ai.task && !ai.campaignUsed ? ai.reloadTask : undefined}
      />
    );
  }

  if (ai.task && !ai.running && ai.task.state !== 'completed' && ai.errorGuidance) {
    return (
      <Alert
        showIcon
        type={
          ai.task.state === 'cancelled'
            ? 'info'
            : ai.task.state === 'timed_out'
              ? 'warning'
              : 'error'
        }
        title={ai.errorGuidance.title}
        description={ai.errorGuidance.detail}
        action={
          ai.errorGuidance.refreshAssessment ? (
            <Button onClick={onRefreshAssessment}>刷新测评</Button>
          ) : undefined
        }
      />
    );
  }

  return null;
}

function formatPercent(rate: number | null) {
  return rate === null ? '—' : `${(rate * 100).toFixed(1)}%`;
}

function averageScoreText(item: AnswerSheetPattern) {
  if (!item.score_rate_available) return '满分标准不一致';
  return item.average_score !== null ? String(item.average_score) : '暂无得分数据';
}

export function ProblemAnalysisView({
  patterns,
  onDrillToStudents,
}: {
  patterns: readonly AnswerSheetPattern[];
  onDrillToStudents?: (filter: StudentDrillFilter) => void;
}) {
  const problems = useMemo(
    () =>
      [...patterns].sort((a, b) => {
        const rateA = a.failure_rate ?? -1;
        const rateB = b.failure_rate ?? -1;
        if (rateB !== rateA) return rateB - rateA;
        return a.ordinal - b.ordinal;
      }),
    [patterns],
  );

  const overall = useMemo(() => {
    let totalSubmissions = 0;
    let totalTestPoints = 0;
    let totalObservations = 0;
    let totalEffective = 0;
    let totalPass = 0;
    let totalFail = 0;
    let totalPartial = 0;
    let totalNotAttempted = 0;
    let totalUnknown = 0;
    let totalFullPass = 0;
    let totalZeroScore = 0;
    const verdictMap = new Map<string, number>();

    for (const item of problems) {
      totalSubmissions += item.submission_count;
      totalTestPoints += item.test_point_count;
      totalObservations += item.observation_count;
      totalEffective += item.effective_count;
      totalPass += item.pass_count;
      totalFail += item.fail_count;
      totalPartial += item.partial_count;
      totalNotAttempted += item.not_attempted_count;
      totalUnknown += item.unknown_count;
      totalFullPass += item.full_pass_count;
      totalZeroScore += item.zero_score_count;
      for (const entry of item.verdict_counts) {
        const status = entry.status || '未知';
        verdictMap.set(status, (verdictMap.get(status) ?? 0) + entry.count);
      }
    }

    const overallFailureRate = totalEffective > 0 ? totalFail / totalEffective : null;
    const overallCoverage = totalObservations > 0 ? totalEffective / totalObservations : null;
    const overallVerdicts = Array.from(verdictMap.entries())
      .map(([status, count]) => ({ status, count }))
      .sort((a, b) => b.count - a.count || a.status.localeCompare(b.status));

    return {
      totalSubmissions,
      totalTestPoints,
      totalObservations,
      totalEffective,
      totalPass,
      totalFail,
      totalPartial,
      totalNotAttempted,
      totalUnknown,
      totalFullPass,
      totalZeroScore,
      overallFailureRate,
      overallCoverage,
      overallVerdicts,
    };
  }, [problems]);

  const [selectedLabel, setSelectedLabel] = useState<string | null>(null);

  if (!problems.length) {
    return <Empty description="暂无题目错误统计结果" />;
  }

  const current = problems.find((item) => item.problem_label === selectedLabel) ?? problems[0]!;

  return (
    <Flex vertical gap={tokens.size}>
      <Card
        size="small"
        type="inner"
        title="本次题目整体情况"
        extra={<Tag>共 {problems.length} 道题</Tag>}
      >
        <Flex vertical gap={tokens.sizeSM}>
          <Row gutter={[tokens.sizeSM, tokens.sizeSM]}>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="总提交份数" value={overall.totalSubmissions} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="测试点整体失败率"
                  value={formatPercent(overall.overallFailureRate)}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="测试点有效覆盖率"
                  value={formatPercent(overall.overallCoverage)}
                />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic
                  title="满分 / 零分提交"
                  value={`${overall.totalFullPass} / ${overall.totalZeroScore}`}
                />
              </Card>
            </Col>
          </Row>

          {overall.overallVerdicts.length > 0 && (
            <Flex align="center" wrap gap={tokens.sizeXS}>
              <Typography.Text type="secondary">整体判题分布：</Typography.Text>
              {overall.overallVerdicts.map((entry) => (
                <Tag key={entry.status} color={verdictTagColor(entry.status)}>
                  {entry.status} {entry.count}
                </Tag>
              ))}
            </Flex>
          )}

          <Flex align="center" wrap gap={tokens.sizeXS}>
            <Typography.Text type="secondary">测试点观测汇总：</Typography.Text>
            <Tag>测试点总数 {overall.totalTestPoints}</Tag>
            <Tag color="success">通过 {overall.totalPass}</Tag>
            <Tag color="error">失败 {overall.totalFail}</Tag>
            {overall.totalPartial > 0 && <Tag color="warning">部分正确 {overall.totalPartial}</Tag>}
            {overall.totalNotAttempted > 0 && <Tag>未作答 {overall.totalNotAttempted}</Tag>}
            {overall.totalUnknown > 0 && <Tag>未知 {overall.totalUnknown}</Tag>}
          </Flex>
        </Flex>
      </Card>

      <Flex vertical gap={tokens.sizeSM}>
        <Typography.Text strong>每道题详细情况</Typography.Text>

        <Row gutter={[tokens.size, tokens.size]}>
          <Col xs={24} lg={7}>
            <Card
              size="small"
              type="inner"
              title="题目列表"
              extra={<Typography.Text type="secondary">{problems.length} 题</Typography.Text>}
            >
              <Flex
                vertical
                gap={tokens.sizeXXS}
                style={{ maxHeight: 620, overflowY: 'auto', paddingRight: 2 }}
              >
                {problems.map((item) => {
                  const active = item.problem_label === current.problem_label;
                  const rateLabel =
                    item.failure_rate !== null ? `失败 ${formatPercent(item.failure_rate)}` : null;
                  return (
                    <Button
                      key={item.problem_label}
                      block
                      color={active ? 'primary' : 'default'}
                      variant={active ? 'filled' : 'text'}
                      style={{
                        height: 'auto',
                        paddingBlock: tokens.sizeXS,
                        paddingInline: tokens.sizeSM,
                        textAlign: 'left',
                        justifyContent: 'space-between',
                      }}
                      aria-pressed={active}
                      onClick={() => setSelectedLabel(item.problem_label)}
                    >
                      <Flex vertical align="flex-start" gap={2} style={{ minWidth: 0, flex: 1 }}>
                        <Flex
                          align="center"
                          justify="space-between"
                          gap={tokens.sizeXS}
                          style={{ width: '100%' }}
                        >
                          <Typography.Text
                            strong={active}
                            style={{
                              color: active ? 'var(--primary-text)' : undefined,
                              overflowWrap: 'anywhere',
                              whiteSpace: 'normal',
                              textAlign: 'left',
                            }}
                          >
                            {item.problem_label}
                          </Typography.Text>
                          {rateLabel && (
                            <Tag
                              color={item.failure_rate! > 0 ? 'volcano' : 'success'}
                              style={{ marginInlineEnd: 0, flexShrink: 0 }}
                            >
                              {rateLabel}
                            </Tag>
                          )}
                        </Flex>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          {[
                            `${item.submission_count} 份提交`,
                            `${item.test_point_count} 个测试点`,
                            item.score_rate_available && item.average_score !== null
                              ? `均分 ${item.average_score}`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </Typography.Text>
                      </Flex>
                    </Button>
                  );
                })}
              </Flex>
            </Card>
          </Col>

          <Col xs={24} lg={17}>
            <Card
              size="small"
              type="inner"
              title={`${current.problem_label} · 题目详情`}
              extra={
                <Space wrap size="small">
                  <Tag>{current.submission_count} 份提交</Tag>
                  <Tag>{current.test_point_count} 个测试点</Tag>
                  {current.failure_rate !== null && (
                    <Tag color={current.failure_rate > 0 ? 'volcano' : 'success'}>
                      失败率 {formatPercent(current.failure_rate)}
                    </Tag>
                  )}
                </Space>
              }
            >
              <Flex vertical gap={tokens.sizeSM}>
                <Descriptions
                  size="small"
                  bordered
                  column={{ xs: 1, sm: 2, xl: 4 }}
                  items={[
                    {
                      key: 'submission_count',
                      label: '提交份数',
                      children: current.submission_count,
                    },
                    {
                      key: 'full_pass_count',
                      label: '满分提交',
                      children: current.full_pass_count,
                    },
                    {
                      key: 'zero_score_count',
                      label: '零分提交',
                      children: current.zero_score_count,
                    },
                    { key: 'average_score', label: '平均分', children: averageScoreText(current) },
                    {
                      key: 'observation_count',
                      label: '测试点观测总数',
                      children: current.observation_count,
                    },
                    {
                      key: 'effective_count',
                      label: '有效观测数',
                      children: current.effective_count,
                    },
                    {
                      key: 'coverage',
                      label: '有效覆盖率',
                      children: formatPercent(current.coverage),
                    },
                    {
                      key: 'unknown_rate',
                      label: '未知状态占比',
                      children: formatPercent(current.unknown_rate),
                    },
                  ]}
                />

                {current.verdict_counts.length > 0 && (
                  <Flex align="center" wrap gap={tokens.sizeXS}>
                    <Typography.Text type="secondary">判题结果分布：</Typography.Text>
                    {current.verdict_counts.map((entry) => {
                      const pct =
                        current.submission_count > 0
                          ? Math.round((entry.count / current.submission_count) * 100)
                          : 0;
                      const tagText = `${entry.status || '未知'} ${entry.count} (${pct}%)`;
                      if (!onDrillToStudents || !entry.status || entry.status === '未标注') {
                        return (
                          <Tag
                            key={entry.status || 'unknown'}
                            color={verdictTagColor(entry.status)}
                          >
                            {tagText}
                          </Tag>
                        );
                      }
                      return (
                        <Tag
                          key={entry.status}
                          color={verdictTagColor(entry.status)}
                          role="button"
                          tabIndex={0}
                          style={{ cursor: 'pointer' }}
                          onClick={() =>
                            onDrillToStudents({
                              problemLabel: current.problem_label,
                              status: entry.status,
                            })
                          }
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              onDrillToStudents({
                                problemLabel: current.problem_label,
                                status: entry.status,
                              });
                            }
                          }}
                        >
                          {tagText}
                        </Tag>
                      );
                    })}
                    {onDrillToStudents && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        点击结论标签可筛选对应学生
                      </Typography.Text>
                    )}
                  </Flex>
                )}

                <Flex align="center" wrap gap={tokens.sizeXS}>
                  <Typography.Text type="secondary">测试点状态明细：</Typography.Text>
                  <Tag color="success">通过 {current.pass_count}</Tag>
                  <Tag color="error">失败 {current.fail_count}</Tag>
                  <Tag color="warning">部分正确 {current.partial_count}</Tag>
                  <Tag>未作答 {current.not_attempted_count}</Tag>
                  <Tag>未知 {current.unknown_count}</Tag>
                </Flex>

                <Flex vertical gap={tokens.sizeXS}>
                  <Typography.Text type="secondary">高频失败测试点：</Typography.Text>
                  {current.worst_test_points.length ? (
                    <Flex vertical gap={tokens.sizeXXS}>
                      {current.worst_test_points.map((point) => {
                        const pct =
                          current.failure_count > 0
                            ? Math.round((point.failure_count / current.failure_count) * 100)
                            : 0;
                        return (
                          <Flex key={point.test_index} align="center" gap={tokens.sizeSM} wrap>
                            <Tag color="warning" style={{ marginInlineEnd: 0 }}>
                              第 {point.test_index} 点
                            </Tag>
                            <Typography.Text style={{ minWidth: 72 }}>
                              {point.failure_count} 次失败
                            </Typography.Text>
                            <Progress
                              percent={pct}
                              status="exception"
                              size="small"
                              style={{ flex: 1, minWidth: 140, maxWidth: 260, marginBottom: 0 }}
                            />
                            {onDrillToStudents && (
                              <Button
                                type="link"
                                size="small"
                                onClick={() =>
                                  onDrillToStudents({
                                    problemLabel: current.problem_label,
                                    testIndex: point.test_index,
                                  })
                                }
                              >
                                查看学生
                              </Button>
                            )}
                          </Flex>
                        );
                      })}
                    </Flex>
                  ) : (
                    <Typography.Text type="secondary">暂无明确失败的测试点</Typography.Text>
                  )}
                </Flex>
              </Flex>
            </Card>
          </Col>
        </Row>
      </Flex>
    </Flex>
  );
}

export function AiCheckView({
  students,
  sourceSnapshotId,
  ai,
  onInspectStudent,
  onRefreshAssessment,
}: {
  students: readonly AnswerSheetStudentRow[];
  sourceSnapshotId: string | null;
  ai: AiHookState;
  onInspectStudent: (studentPk: string, focusProblem?: string) => void;
  onRefreshAssessment: () => void;
}) {
  const studentByKey = useMemo(
    () => new Map(students.map((student) => [student.external_key, student])),
    [students],
  );

  return (
    <Flex vertical gap={tokens.size}>
      <Flex align="center" justify="space-between" wrap gap={tokens.sizeSM}>
        <Typography.Text type="secondary">
          针对当前编程答题卡快照检索共性异常与高风险学生代码证据；每次上传快照最多执行一次调查。
        </Typography.Text>
        <ProgrammingAiActionButton ai={ai} onRefreshAssessment={onRefreshAssessment} />
      </Flex>

      <ProgrammingAiStatusBanner
        ai={ai}
        sourceSnapshotId={sourceSnapshotId}
        onRefreshAssessment={onRefreshAssessment}
      />

      {!ai.completedResult && !ai.running && !ai.error && ai.task?.state !== 'cancelled' && (
        <Card size="small" type="inner">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              ai.restoring
                ? '正在同步上次调查记录…'
                : '尚未完成 AI 核查，点击「发起编程调查」开始检索可验证的学生代码证据。'
            }
          />
        </Card>
      )}

      {ai.completedResult && (
        <Flex vertical gap={tokens.sizeSM}>
          <Alert type="success" showIcon title={ai.completedResult.statement} />
          {ai.completedResult.findings.length > 0 ? (
            ai.completedResult.findings.map((finding) => (
              <FindingCard
                key={finding.finding_id}
                finding={finding}
                studentByKey={studentByKey}
                onInspectStudent={onInspectStudent}
              />
            ))
          ) : (
            <Card size="small" type="inner">
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={ai.completedResult.statement}
              />
            </Card>
          )}
        </Flex>
      )}

      {ai.completedResult && ai.task && (
        <Disclosure className="ai-foldout ai-technical" label={<>运行记录</>}>
          <dl>
            <dt>执行来源</dt>
            <dd>{executionOriginText(ai.task.execution_origin)}</dd>
            <dt>工具核查</dt>
            <dd>{ai.task.tool_attempts} 次</dd>
            <dt>模型请求</dt>
            <dd>{ai.task.model_requests} 次</dd>
          </dl>
        </Disclosure>
      )}
    </Flex>
  );
}

function FindingCard({
  finding,
  problemLabel,
  studentByKey,
  onInspectStudent,
}: {
  finding: ProgrammingFinding;
  problemLabel?: string;
  studentByKey: Map<string, ProgrammingStudentRef>;
  onInspectStudent: (studentPk: string, focusProblem?: string) => void;
}) {
  const scopedEvidence = useMemo(() => {
    if (!problemLabel) return finding.evidence;
    const filtered = finding.evidence.filter((item) => item.problem_label === problemLabel);
    return filtered.length ? filtered : finding.evidence;
  }, [finding.evidence, problemLabel]);

  const titleLabel =
    problemLabel ||
    Array.from(new Set(scopedEvidence.map((item) => item.problem_label))).join(' · ') ||
    'AI 核查发现';

  const testLabels = Array.from(
    new Set(scopedEvidence.map((item) => formatTestTag(item.test_index))),
  );

  return (
    <Card
      size="small"
      type="inner"
      className="programming-finding-card"
      data-testid="programming-finding-card"
      title={titleLabel}
      extra={
        <Space wrap size="small">
          <Tag color="volcano">{phenomenonLabel(finding.code)}</Tag>
          {testLabels.slice(0, 4).map((label) => (
            <Tag key={label}>{label}</Tag>
          ))}
          {testLabels.length > 4 && <Tag>等 {testLabels.length} 处</Tag>}
        </Space>
      }
    >
      <Flex vertical gap={tokens.sizeSM}>
        <Typography.Paragraph style={{ marginBottom: 0 }}>{finding.statement}</Typography.Paragraph>

        {scopedEvidence.length > 0 && (
          <Disclosure defaultExpanded label={<>关联学生证据（{scopedEvidence.length} 条）</>}>
            <Table<ProgrammingEvidence & { rowKey: string }>
              rowKey="rowKey"
              dataSource={scopedEvidence.map((item, idx) => ({
                ...item,
                rowKey: `${finding.finding_id}:${item.asset_id}:${item.student_key}:${item.test_index ?? 'v'}:${idx}`,
              }))}
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 6, hideOnSinglePage: true, showSizeChanger: false }}
              columns={[
                {
                  title: '学生',
                  render: (_, item) => {
                    const matched = studentByKey.get(item.student_key);
                    return (
                      <Flex vertical gap={tokens.marginXXS}>
                        <Typography.Text strong>
                          {matched?.display_name || item.student_key}
                        </Typography.Text>
                        {matched?.display_name && (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {item.student_key}
                          </Typography.Text>
                        )}
                      </Flex>
                    );
                  },
                },
                {
                  title: '定位',
                  render: (_, item) =>
                    !problemLabel
                      ? `${item.problem_label} · ${evidenceLocationText(item)}`
                      : evidenceLocationText(item),
                },
                {
                  title: '来源文件',
                  dataIndex: 'source_entry',
                },
                {
                  title: '操作',
                  render: (_, item) => {
                    const matched = studentByKey.get(item.student_key);
                    return matched ? (
                      <Button
                        type="link"
                        size="small"
                        onClick={() => onInspectStudent(matched.student_pk, item.problem_label)}
                      >
                        查看个人证据
                      </Button>
                    ) : (
                      <Typography.Text type="secondary">未匹配参与者</Typography.Text>
                    );
                  },
                },
              ]}
            />
          </Disclosure>
        )}
      </Flex>
    </Card>
  );
}

type ProblemStudentItem = Decoded<typeof answerSheetProblemStudentsDto>['items'][number];

interface ExplorerStudentRow {
  rowKey: string;
  student_pk: string;
  display_name: string;
  external_key: string;
  verdict: string | null;
  score: number | null;
  score_total: number | null;
  failed_test_points: number[];
  focus: ProgrammingStudentFocus | null;
  attemptText: string;
}

function observationTagColor(status: string) {
  if (/通过|正确|accepted|pass|\bok\b/i.test(status)) return 'success';
  if (/部分|partial/i.test(status)) return 'warning';
  if (/错|超时|超限|异常|失败|wrong|timeout|error|limit|fail/i.test(status)) return 'error';
  return 'default';
}

export function StudentAnalysisView({
  assessmentId,
  sourceSnapshotId,
  patterns,
  students,
  ai,
  drillFilter,
  onClearDrillFilter,
  onRefreshAssessment,
}: {
  assessmentId: string;
  sourceSnapshotId: string | null;
  patterns: readonly AnswerSheetPattern[];
  students: readonly AnswerSheetStudentRow[];
  ai: AiHookState;
  drillFilter: StudentDrillFilter | null;
  onClearDrillFilter: () => void;
  onRefreshAssessment: () => void;
}) {
  const { client } = useDataMode();
  const [selectedProblem, setSelectedProblem] = useState<string | null>(
    () => drillFilter?.problemLabel ?? patterns[0]?.problem_label ?? null,
  );
  const [statusFilter, setStatusFilter] = useState<string | null>(
    () => drillFilter?.status ?? null,
  );
  const [testIndexFilter, setTestIndexFilter] = useState<number | null>(
    () => drillFilter?.testIndex ?? null,
  );
  const [scopeOverride, setScopeOverride] = useState<'all' | 'focus' | null>(null);
  const [search, setSearch] = useState('');
  const [selectedPk, setSelectedPk] = useState<string | null>(null);
  const [selectedSubmissionLabel, setSelectedSubmissionLabel] = useState<string | null>(null);
  const [prevDrill, setPrevDrill] = useState(drillFilter);
  if (prevDrill !== drillFilter) {
    setPrevDrill(drillFilter);
    if (drillFilter) {
      if (drillFilter.problemLabel) setSelectedProblem(drillFilter.problemLabel);
      setStatusFilter(drillFilter.status ?? null);
      setTestIndexFilter(drillFilter.testIndex ?? null);
      setScopeOverride('all');
      setSelectedPk(null);
      setSelectedSubmissionLabel(null);
    }
  }

  const activeProblem = patterns.some((item) => item.problem_label === selectedProblem)
    ? selectedProblem
    : (patterns[0]?.problem_label ?? null);

  const problemKey = activeProblem
    ? `prob-students:${assessmentId}:${activeProblem}:${statusFilter ?? ''}:${testIndexFilter ?? ''}`
    : null;
  const loadProblemStudents = useCallback(
    (signal: AbortSignal) =>
      client.answerSheetProblemStudents(
        assessmentId,
        activeProblem!,
        {
          status: statusFilter ?? undefined,
          testIndex: testIndexFilter ?? undefined,
          offset: 0,
          limit: 100,
        },
        signal,
      ),
    [client, assessmentId, activeProblem, statusFilter, testIndexFilter],
  );
  const problemStudents = useRemote(problemKey, loadProblemStudents);

  const focusList = useMemo(() => ai.summary?.students ?? [], [ai.summary]);
  const focusCount = focusList.length;
  const activeScope: 'all' | 'focus' = focusCount > 0 ? (scopeOverride ?? 'focus') : 'all';

  const currentPattern = useMemo(
    () => patterns.find((item) => item.problem_label === activeProblem) ?? null,
    [patterns, activeProblem],
  );

  const verdictOptions = useMemo(() => {
    const seen = new Map<string, number>();
    for (const entry of currentPattern?.verdict_counts ?? []) {
      if (entry.status && entry.status !== '未标注') seen.set(entry.status, entry.count);
    }
    if (statusFilter && !seen.has(statusFilter)) seen.set(statusFilter, 0);
    return Array.from(seen.entries()).map(([status, cnt]) => ({
      value: status,
      label: cnt > 0 ? `${status} (${cnt})` : status,
    }));
  }, [currentPattern, statusFilter]);

  const testPointOptions = useMemo(() => {
    const failMap = new Map<number, number>();
    for (const point of currentPattern?.worst_test_points ?? []) {
      failMap.set(point.test_index, point.failure_count);
    }
    const indices = new Set<number>(failMap.keys());
    const count = currentPattern?.test_point_count ?? 0;
    for (let i = 1; i <= count; i++) indices.add(i);
    if (testIndexFilter !== null) indices.add(testIndexFilter);
    return Array.from(indices)
      .sort((a, b) => a - b)
      .map((index) => {
        const failures = failMap.get(index);
        return {
          value: index,
          label: failures != null ? `第 ${index} 点 (${failures} 次失败)` : `第 ${index} 点`,
        };
      });
  }, [currentPattern, testIndexFilter]);

  const matched = useMemo<ExplorerStudentRow[]>(() => {
    const metaByPk = new Map(students.map((student) => [student.student_pk, student]));
    const focusByKey = new Map(focusList.map((item) => [item.student_key, item]));

    const buildAttemptText = (meta?: AnswerSheetStudentRow) =>
      meta
        ? [
            meta.started_at && `开始 ${meta.started_at}`,
            meta.finished_at && `结束 ${meta.finished_at}`,
          ]
            .filter(Boolean)
            .join(' · ')
        : '';

    let combined: ExplorerStudentRow[];
    if (activeProblem) {
      const apiItems: readonly ProblemStudentItem[] = problemStudents.data?.items ?? [];
      combined = apiItems.map((item) => {
        const meta = metaByPk.get(item.student_pk);
        return {
          rowKey: item.student_pk,
          student_pk: item.student_pk,
          display_name: item.display_name,
          external_key: item.external_key,
          verdict: item.verdict,
          score: item.score,
          score_total: item.score_total,
          failed_test_points: item.failed_test_points,
          focus: focusByKey.get(item.external_key) ?? null,
          attemptText: buildAttemptText(meta),
        };
      });
    } else {
      combined = students.map((student) => ({
        rowKey: student.student_pk,
        student_pk: student.student_pk,
        display_name: student.display_name,
        external_key: student.external_key,
        verdict: null,
        score: null,
        score_total: null,
        failed_test_points: [],
        focus: focusByKey.get(student.external_key) ?? null,
        attemptText: buildAttemptText(student),
      }));
    }

    const q = search.trim().toLowerCase();
    return combined.filter((row) => {
      if (activeScope === 'focus' && !row.focus) return false;
      if (!q) return true;
      return (
        row.display_name.toLowerCase().includes(q) || row.external_key.toLowerCase().includes(q)
      );
    });
  }, [activeProblem, activeScope, focusList, problemStudents.data, search, students]);

  const activeStudent = useMemo(
    () => matched.find((item) => item.student_pk === selectedPk) ?? matched[0] ?? null,
    [matched, selectedPk],
  );

  const evidenceKey = activeStudent
    ? `student-inline-evidence:${assessmentId}:${activeStudent.student_pk}:${sourceSnapshotId ?? 'none'}`
    : null;
  const loadEvidence = useCallback(
    (signal: AbortSignal) =>
      client.answerSheetEvidence(assessmentId, activeStudent!.student_pk, sourceSnapshotId, signal),
    [client, assessmentId, activeStudent, sourceSnapshotId],
  );
  const evidence = useRemote(evidenceKey, loadEvidence);
  const stale =
    evidence.error?.code === 'SOURCE_CHANGED' || evidence.error?.code === 'ORIGINAL_UNAVAILABLE';

  const ratio = students.length > 0 ? Math.round((matched.length / students.length) * 100) : 0;

  function resetFilters() {
    setStatusFilter(null);
    setTestIndexFilter(null);
    setSearch('');
    setScopeOverride('all');
    setSelectedPk(null);
    setSelectedSubmissionLabel(null);
    onClearDrillFilter();
  }

  const orderedSubmissions = useMemo(
    () => [...(evidence.data?.submissions ?? [])].sort((a, b) => a.ordinal - b.ordinal),
    [evidence.data],
  );

  const currentSubmission = useMemo(() => {
    if (!orderedSubmissions.length) return null;
    if (selectedSubmissionLabel) {
      const explicit = orderedSubmissions.find(
        (sub) => sub.problem_label === selectedSubmissionLabel,
      );
      if (explicit) return explicit;
    }
    if (activeProblem) {
      const focused = orderedSubmissions.find((sub) => sub.problem_label === activeProblem);
      if (focused) return focused;
    }
    return orderedSubmissions[0]!;
  }, [orderedSubmissions, selectedSubmissionLabel, activeProblem]);

  const evidenceSummary = useMemo(() => {
    const subs = evidence.data?.submissions ?? [];
    let totalObs = 0;
    let passedObs = 0;
    for (const sub of subs) {
      totalObs += sub.observations.length;
      for (const obs of sub.observations) {
        if (/通过|正确|accepted|pass|\bok\b/i.test(obs.status)) passedObs += 1;
      }
    }
    return { submissionCount: subs.length, totalObs, passedObs };
  }, [evidence.data]);

  return (
    <Flex vertical gap={tokens.size}>
      <Card size="small" type="inner">
        <Flex align="center" justify="space-between" wrap gap={tokens.sizeSM}>
          <Flex align="center" wrap gap={tokens.sizeXS}>
            {patterns.length > 0 && (
              <Select
                aria-label="筛选题目"
                value={activeProblem ?? undefined}
                onChange={(value) => {
                  setSelectedProblem(value);
                  setStatusFilter(null);
                  setTestIndexFilter(null);
                  setSelectedPk(null);
                  setSelectedSubmissionLabel(null);
                }}
                style={{ minWidth: 160 }}
                options={patterns.map((item) => ({
                  value: item.problem_label,
                  label: item.problem_label,
                }))}
              />
            )}
            <Select
              allowClear
              aria-label="筛选判题结论"
              placeholder="全部判题结果"
              value={statusFilter ?? undefined}
              onChange={(value) => {
                setStatusFilter(value ?? null);
                setSelectedPk(null);
              }}
              style={{ minWidth: 150 }}
              options={verdictOptions}
            />
            <Select
              allowClear
              aria-label="筛选失败测试点"
              placeholder="全部测试点"
              value={testIndexFilter ?? undefined}
              onChange={(value) => {
                setTestIndexFilter(value ?? null);
                setSelectedPk(null);
              }}
              style={{ minWidth: 160 }}
              options={testPointOptions}
            />
            <Input.Search
              allowClear
              aria-label="搜索姓名或学号"
              placeholder="搜索姓名或学号"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              style={{ width: 200 }}
            />
            {focusCount > 0 && (
              <Segmented<'focus' | 'all'>
                aria-label="学生范围"
                style={{ fontWeight: 'normal' }}
                value={activeScope}
                onChange={(value) => setScopeOverride(value)}
                options={[
                  { value: 'focus', label: `AI 关注 (${focusCount})` },
                  { value: 'all', label: '全部学生' },
                ]}
              />
            )}
            <Button onClick={resetFilters}>重置</Button>
          </Flex>
          <Typography.Text type="secondary">
            当前匹配 <Typography.Text strong>{matched.length}</Typography.Text> / {students.length}{' '}
            人 · 占参评人数 {ratio}%
            {problemStudents.data && problemStudents.data.total_count > 100 && '（仅显示前 100）'}
          </Typography.Text>
        </Flex>
      </Card>

      <Row gutter={[tokens.size, tokens.size]}>
        <Col xs={24} lg={7}>
          <Card
            size="small"
            type="inner"
            title="匹配学生"
            extra={<Typography.Text type="secondary">{matched.length} 人</Typography.Text>}
          >
            {problemStudents.loading && <LoadingPanel />}
            {problemStudents.error && (
              <RequestError error={problemStudents.error} retry={problemStudents.retry} />
            )}
            {!problemStudents.loading &&
              !problemStudents.error &&
              (matched.length ? (
                <Flex
                  vertical
                  gap={tokens.sizeXXS}
                  style={{ maxHeight: 620, overflowY: 'auto', paddingRight: 2 }}
                >
                  {matched.map((row) => {
                    const active = activeStudent?.student_pk === row.student_pk;
                    const scoreText =
                      row.score !== null
                        ? row.score_total !== null
                          ? `得分 ${row.score}/${row.score_total}`
                          : `得分 ${row.score}`
                        : null;
                    const failedText =
                      row.failed_test_points.length > 0
                        ? `未过第 ${row.failed_test_points.slice(0, 3).join('、')} 点${row.failed_test_points.length > 3 ? '等' : ''}`
                        : null;
                    return (
                      <Button
                        key={row.rowKey}
                        block
                        color={active ? 'primary' : 'default'}
                        variant={active ? 'filled' : 'text'}
                        style={{
                          height: 'auto',
                          paddingBlock: tokens.sizeXS,
                          paddingInline: tokens.sizeSM,
                          textAlign: 'left',
                          justifyContent: 'space-between',
                        }}
                        aria-pressed={active}
                        onClick={() => setSelectedPk(row.student_pk)}
                      >
                        <Flex vertical align="flex-start" gap={2} style={{ minWidth: 0, flex: 1 }}>
                          <Flex
                            align="center"
                            justify="space-between"
                            gap={tokens.sizeXS}
                            style={{ width: '100%' }}
                          >
                            <Typography.Text
                              strong={active}
                              style={{
                                color: active ? 'var(--primary-text)' : undefined,
                                overflowWrap: 'anywhere',
                              }}
                            >
                              {row.display_name || row.external_key || '未命名参与者'}
                            </Typography.Text>
                            <Space size={4} style={{ flexShrink: 0 }}>
                              {row.focus && <Tag color="warning">AI 关注</Tag>}
                              {row.verdict && (
                                <Tag
                                  color={verdictTagColor(row.verdict)}
                                  style={{ marginInlineEnd: 0 }}
                                >
                                  {row.verdict}
                                </Tag>
                              )}
                            </Space>
                          </Flex>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {[row.external_key, scoreText, failedText]
                              .filter(Boolean)
                              .join(' · ') || '—'}
                          </Typography.Text>
                        </Flex>
                      </Button>
                    );
                  })}
                </Flex>
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="没有符合当前筛选条件的学生"
                />
              ))}
          </Card>
        </Col>

        <Col xs={24} lg={17}>
          <Card
            size="small"
            type="inner"
            title={
              activeStudent
                ? `${activeStudent.display_name || activeStudent.external_key} · 答题情况`
                : '学生答题情况'
            }
          >
            {!activeStudent ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="请在左侧选择学生查看答题情况"
              />
            ) : (
              <Flex vertical gap={tokens.size}>
                <Flex align="center" justify="space-between" wrap gap={tokens.sizeXS}>
                  <Space wrap size="small">
                    {activeStudent.external_key && <Tag>{activeStudent.external_key}</Tag>}
                    {activeStudent.attemptText && (
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {activeStudent.attemptText}
                      </Typography.Text>
                    )}
                  </Space>
                  {activeStudent.focus && (
                    <Tag color="warning">
                      AI 重点关注 · {activeStudent.focus.finding_count} 项（
                      {activeStudent.focus.problem_labels.join('、')}）
                    </Tag>
                  )}
                </Flex>

                {evidence.loading && <LoadingPanel />}
                {evidence.error &&
                  (stale ? (
                    <Alert
                      type="warning"
                      title={
                        evidence.error.code === 'SOURCE_CHANGED'
                          ? '资料来源已变化'
                          : '原件当前不可用'
                      }
                      action={<Button onClick={onRefreshAssessment}>刷新测评</Button>}
                    />
                  ) : (
                    <RequestError error={evidence.error} retry={evidence.retry} />
                  ))}

                {evidence.data && (
                  <Flex vertical gap={tokens.sizeSM}>
                    <Descriptions
                      size="small"
                      bordered
                      column={{ xs: 1, sm: 2, md: 4 }}
                      items={[
                        {
                          key: 'subs',
                          label: '提交题数',
                          children: evidenceSummary.submissionCount,
                        },
                        {
                          key: 'obs',
                          label: '测试点通过',
                          children:
                            evidenceSummary.totalObs > 0
                              ? `${evidenceSummary.passedObs} / ${evidenceSummary.totalObs}`
                              : '—',
                        },
                        {
                          key: 'verdict',
                          label: activeProblem ? `${activeProblem} 判题` : '当前题判题',
                          children: activeStudent.verdict ? (
                            <Tag color={verdictTagColor(activeStudent.verdict)}>
                              {activeStudent.verdict}
                            </Tag>
                          ) : (
                            '—'
                          ),
                        },
                        {
                          key: 'score',
                          label: activeProblem ? `${activeProblem} 得分` : '当前题得分',
                          children:
                            activeStudent.score !== null
                              ? activeStudent.score_total !== null
                                ? `${activeStudent.score} / ${activeStudent.score_total}`
                                : String(activeStudent.score)
                              : '—',
                        },
                      ]}
                    />

                    {!orderedSubmissions.length && (
                      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无提交记录" />
                    )}

                    {orderedSubmissions.length > 0 && currentSubmission && (
                      <Flex vertical gap={tokens.sizeSM}>
                        <Flex align="center" wrap gap={tokens.sizeXS}>
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            作答题目：
                          </Typography.Text>
                          {orderedSubmissions.map((submission) => {
                            const selected =
                              submission.problem_label === currentSubmission.problem_label;
                            const statusType = verdictTagColor(submission.verdict || '');
                            const dotColor =
                              statusType === 'success'
                                ? tokens.colorSuccess
                                : statusType === 'error'
                                  ? tokens.colorError
                                  : statusType === 'warning'
                                    ? tokens.colorWarning
                                    : tokens.colorTextQuaternary;
                            return (
                              <Button
                                key={`${submission.ordinal}:${submission.problem_label}`}
                                size="small"
                                color={selected ? 'primary' : 'default'}
                                variant={selected ? 'filled' : 'outlined'}
                                aria-pressed={selected}
                                onClick={() => setSelectedSubmissionLabel(submission.problem_label)}
                              >
                                <Flex align="center" gap={6}>
                                  <span
                                    aria-hidden="true"
                                    style={{
                                      width: 6,
                                      height: 6,
                                      borderRadius: '50%',
                                      backgroundColor: dotColor,
                                      display: 'inline-block',
                                    }}
                                  />
                                  <span>{submission.problem_label}</span>
                                </Flex>
                              </Button>
                            );
                          })}
                        </Flex>

                        <StudentSubmissionCard
                          key={`${currentSubmission.ordinal}:${currentSubmission.problem_label}`}
                          submission={currentSubmission}
                        />
                      </Flex>
                    )}
                  </Flex>
                )}
              </Flex>
            )}
          </Card>
        </Col>
      </Row>
    </Flex>
  );
}

type AnswerSheetSubmission = Decoded<typeof answerSheetEvidenceDto>['submissions'][number];

function StudentSubmissionCard({ submission }: { submission: AnswerSheetSubmission }) {
  const passObs = submission.observations.filter((obs) =>
    /通过|正确|accepted|pass|\bok\b/i.test(obs.status),
  ).length;
  const metaLine = [
    submission.source?.entry,
    submission.source?.source_index != null ? `题块 ${submission.source.source_index}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <Card
      size="small"
      title={submission.problem_label}
      extra={
        <Space wrap size="small">
          {submission.verdict && (
            <Tag color={verdictTagColor(submission.verdict)}>{submission.verdict}</Tag>
          )}
          {submission.observations.length > 0 && (
            <Tag>
              通过 {passObs} / {submission.observations.length} 点
            </Tag>
          )}
        </Space>
      }
    >
      <Flex vertical gap={tokens.sizeSM}>
        {metaLine && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {metaLine}
          </Typography.Text>
        )}

        {submission.observations.length > 0 ? (
          <Flex wrap gap={tokens.sizeXXS}>
            {submission.observations.map((point, index) => (
              <Tag
                key={`${point.test_index}:${point.source_row ?? index}`}
                color={observationTagColor(point.status)}
              >
                {[
                  point.test_index != null ? `第 ${point.test_index} 点` : null,
                  point.status,
                  point.duration_text,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </Tag>
            ))}
          </Flex>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            暂无测试点记录
          </Typography.Text>
        )}

        {submission.code_state === 'PRESENT' && submission.code ? (
          <Disclosure
            defaultExpanded
            label={
              <>
                源代码{submission.language && ` · ${submission.language}`}
                {submission.code_truncated && '（部分）'}
              </>
            }
          >
            <pre className="answer-sheet-code">
              <code>{submission.code}</code>
            </pre>
          </Disclosure>
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前快照未保留代码原文
          </Typography.Text>
        )}

        {submission.compiler_output && (
          <Disclosure label={<>编译输出{submission.compiler_output_truncated && '（部分）'}</>}>
            <pre className="answer-sheet-code">{submission.compiler_output}</pre>
          </Disclosure>
        )}
      </Flex>
    </Card>
  );
}
