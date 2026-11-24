'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Flex,
  Row,
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
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import {
  phenomenonLabel,
  programmingErrorGuidance,
  programmingTaskRunning,
  programmingTaskStateLabels,
  summarizeProgrammingResult,
  type ProgrammingEvidence,
  type ProgrammingFinding,
  type ProgrammingStudentFocus,
  type ProgrammingStudentRef,
  type ProgrammingTask,
} from '../../services/edu-api/programming-ai-types';
import { LoadingPanel, RequestError } from './LiveStatus';

function storagePointerKey(
  teacherId: string | null,
  assessmentId: string,
  sourceSnapshotId: string | null,
) {
  if (!sourceSnapshotId) return null;
  return `weiran.edu.programming-ai-task.${teacherId ?? 'anon'}.${assessmentId}.${sourceSnapshotId}`;
}

function readStoredTaskId(key: string | null): string | null {
  if (!key) return null;
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStoredTaskId(key: string | null, taskId: string | null) {
  if (!key) return;
  try {
    if (taskId) sessionStorage.setItem(key, taskId);
    else sessionStorage.removeItem(key);
  } catch {
    /* optional browser pointer */
  }
}

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

export default function ProgrammingAiPanel({
  assessmentId,
  sourceSnapshotId,
  students,
  onInspectStudent,
  onRefreshAssessment,
}: {
  assessmentId: string;
  sourceSnapshotId: string | null;
  students: readonly ProgrammingStudentRef[];
  onInspectStudent: (studentPk: string, focusProblem?: string) => void;
  onRefreshAssessment: () => void;
}) {
  const { client, teacherId } = useDataMode();
  const pointerKey = storagePointerKey(teacherId, assessmentId, sourceSnapshotId);
  const [task, setTask] = useState<ProgrammingTask | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(() => Boolean(readStoredTaskId(pointerKey)));
  const pending = useRef<AbortController | null>(null);
  const idempotencyKey = useRef<string | null>(null);

  const validateTaskSnapshot = useCallback(
    (value: ProgrammingTask) => {
      if (value.assessment_id !== assessmentId) {
        throw new ApiError(409, 'AI_SOURCE_CONFLICT');
      }
      if (sourceSnapshotId && value.source_snapshot_id !== sourceSnapshotId) {
        throw new ApiError(409, 'AI_SOURCE_CONFLICT');
      }
      return value;
    },
    [assessmentId, sourceSnapshotId],
  );

  useEffect(() => {
    const savedId = readStoredTaskId(pointerKey);
    if (!savedId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const loaded = validateTaskSnapshot(
          await client.programmingAiTask(assessmentId, savedId, controller.signal),
        );
        if (!controller.signal.aborted) {
          setTask(loaded);
          setError(null);
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (cause instanceof ApiError && (cause.status === 404 || cause.status === 410)) {
          writeStoredTaskId(pointerKey, null);
        } else if (cause instanceof ApiError) {
          setError(cause);
        }
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    })();
    return () => {
      controller.abort();
      if (pending.current) {
        pending.current.abort();
        pending.current = null;
      }
    };
  }, [assessmentId, client, pointerKey, validateTaskSnapshot]);

  useEffect(() => {
    if (!task || !programmingTaskRunning(task) || error) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const next = validateTaskSnapshot(
          await client.programmingAiTask(assessmentId, task.task_id, controller.signal),
        );
        if (!controller.signal.aborted) {
          setTask(next);
        }
      } catch (cause) {
        if (!controller.signal.aborted && cause instanceof ApiError) {
          setError(cause);
        }
      }
    }, 600);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [assessmentId, client, error, task, validateTaskSnapshot]);

  async function operate(work: (signal: AbortSignal) => Promise<void>) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      await work(controller.signal);
    } catch (cause) {
      if (!controller.signal.aborted && cause instanceof ApiError) {
        setError(cause);
      }
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  function startInvestigation() {
    if (!sourceSnapshotId || busy || restoring || task) return;
    if (!idempotencyKey.current) {
      idempotencyKey.current = crypto.randomUUID();
    }
    const key = idempotencyKey.current;
    void operate(async (signal) => {
      const created = validateTaskSnapshot(
        await client.startProgrammingAiTask(assessmentId, key, signal),
      );
      if (signal.aborted) return;
      setTask(created);
      writeStoredTaskId(pointerKey, created.task_id);
    });
  }

  function cancelInvestigation() {
    if (!task || !programmingTaskRunning(task) || busy) return;
    void operate(async (signal) => {
      const cancelled = validateTaskSnapshot(
        await client.cancelProgrammingAiTask(assessmentId, task.task_id, signal),
      );
      if (signal.aborted) return;
      setTask(cancelled);
    });
  }

  const running = programmingTaskRunning(task);
  const completedResult = task?.state === 'completed' ? task.result : null;
  const studentByKey = new Map(students.map((student) => [student.external_key, student]));
  const summary = completedResult ? summarizeProgrammingResult(completedResult, students) : null;
  const campaignUsed = error?.status === 409 && error.code === 'AI_CAMPAIGN_ALREADY_USED';
  const errorGuidance = error
    ? programmingErrorGuidance(error.code)
    : task && !running && task.state !== 'completed'
      ? programmingErrorGuidance(
          task.error_code ??
            (task.state === 'timed_out'
              ? 'AI_TASK_TIMEOUT'
              : task.state === 'cancelled'
                ? 'AI_TASK_CANCELLED'
                : task.state === 'revoked'
                  ? 'AI_PURPOSE_REVOKED'
                  : 'AI_TASK_FAILED'),
        )
      : null;

  const canStart =
    Boolean(sourceSnapshotId) &&
    !task &&
    !restoring &&
    !campaignUsed &&
    !errorGuidance?.refreshAssessment;

  return (
    <Panel
      title="编程 AI 调查"
      className="programming-ai-panel"
      action={
        canStart ? (
          <Button
            type="primary"
            icon={<ExperimentOutlined aria-hidden="true" />}
            loading={busy}
            disabled={busy}
            onClick={startInvestigation}
          >
            发起编程调查
          </Button>
        ) : running && task ? (
          <Button
            icon={<StopOutlined aria-hidden="true" />}
            loading={busy}
            disabled={busy}
            onClick={cancelInvestigation}
          >
            停止调查
          </Button>
        ) : errorGuidance?.refreshAssessment ? (
          <Button icon={<ReloadOutlined aria-hidden="true" />} onClick={onRefreshAssessment}>
            刷新测评
          </Button>
        ) : undefined
      }
    >
      <Flex vertical gap={tokens.size}>
        {restoring && <LoadingPanel />}

        {!restoring && !sourceSnapshotId && (
          <Alert
            type="warning"
            showIcon
            title="当前测评缺少来源快照，暂无法发起编程调查"
            description="请先确认测评资料来源已完成解析并保留原件。"
          />
        )}

        {!restoring && !task && !error && sourceSnapshotId && (
          <Typography.Text type="secondary">
            针对本份编程测评的提交记录、测试点失败与代码特征开展核查，结果仅基于已入库原始答题卡证据生成。
          </Typography.Text>
        )}

        {running && task && !error && (
          <Flex
            align="center"
            justify="space-between"
            wrap
            gap={tokens.sizeSM}
            className="ai-task-actions ai-task-running"
          >
            <Space className="ai-task-status" role="status" size="small">
              <Spin size="small" className="ai-analysis-indicator" aria-hidden="true" />
              <span>{programmingTaskStateLabels[task.state]}</span>
            </Space>
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              核查工具 {task.tool_attempts} 次 · 模型请求 {task.model_requests} 次
            </Typography.Text>
          </Flex>
        )}

        {error &&
          (errorGuidance?.refreshAssessment ? (
            <Alert
              type="error"
              showIcon
              title={errorGuidance.title}
              description={errorGuidance.detail}
              action={<Button onClick={onRefreshAssessment}>刷新测评</Button>}
            />
          ) : (
            <RequestError
              error={error}
              retry={
                task && !campaignUsed
                  ? () =>
                      void operate(async (signal) => {
                        const reloaded = validateTaskSnapshot(
                          await client.programmingAiTask(assessmentId, task.task_id, signal),
                        );
                        if (!signal.aborted) setTask(reloaded);
                      })
                  : undefined
              }
            />
          ))}

        {!error && task && !running && task.state !== 'completed' && errorGuidance && (
          <Alert
            showIcon
            type={
              task.state === 'cancelled' ? 'info' : task.state === 'timed_out' ? 'warning' : 'error'
            }
            title={errorGuidance.title}
            description={errorGuidance.detail}
            action={
              errorGuidance.refreshAssessment ? (
                <Button onClick={onRefreshAssessment}>刷新测评</Button>
              ) : undefined
            }
          />
        )}

        {completedResult && summary && task && (
          <>
            {completedResult.findings.length > 0 ? (
              <>
                <Alert type="success" showIcon title={completedResult.statement} />
                <Row gutter={[tokens.size, tokens.size]}>
                  <Col xs={24} sm={8}>
                    <Card size="small" type="inner">
                      <Statistic title="核查发现" value={summary.finding_count} suffix="条" />
                    </Card>
                  </Col>
                  <Col xs={24} sm={8}>
                    <Card size="small" type="inner">
                      <Statistic
                        title="涉及题目"
                        value={summary.problem_labels.length}
                        suffix="道"
                      />
                    </Card>
                  </Col>
                  <Col xs={24} sm={8}>
                    <Card size="small" type="inner">
                      <Statistic title="关注学生" value={summary.students.length} suffix="人" />
                    </Card>
                  </Col>
                </Row>

                {summary.students.length > 0 && (
                  <Card size="small" type="inner" title="值得进一步关注的学生">
                    <Table<ProgrammingStudentFocus>
                      rowKey="student_key"
                      dataSource={summary.students}
                      scroll={{ x: 'max-content' }}
                      pagination={{ pageSize: 5, hideOnSinglePage: true, showSizeChanger: false }}
                      columns={[
                        {
                          title: '学生',
                          render: (_, item) => (
                            <Flex vertical gap={tokens.marginXXS}>
                              <Typography.Text strong>
                                {item.display_name || item.student_key}
                              </Typography.Text>
                              {item.display_name && (
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  {item.student_key}
                                </Typography.Text>
                              )}
                            </Flex>
                          ),
                        },
                        {
                          title: '涉及题目',
                          render: (_, item) => item.problem_labels.join('、'),
                        },
                        {
                          title: '关联证据',
                          render: (_, item) =>
                            `${item.finding_count} 项发现 · ${item.evidence_count} 条记录`,
                        },
                        {
                          title: '操作',
                          render: (_, item) =>
                            item.student_pk ? (
                              <Button
                                type="link"
                                size="small"
                                onClick={() =>
                                  onInspectStudent(item.student_pk!, item.problem_labels[0])
                                }
                              >
                                查看个人证据
                              </Button>
                            ) : (
                              <Typography.Text type="secondary">未匹配参与者</Typography.Text>
                            ),
                        },
                      ]}
                    />
                  </Card>
                )}

                <Flex vertical gap={tokens.sizeSM}>
                  {completedResult.findings.map((finding, index) => (
                    <FindingCard
                      key={finding.finding_id}
                      finding={finding}
                      index={index}
                      defaultExpanded={completedResult.findings.length <= 2}
                      studentByKey={studentByKey}
                      onInspectStudent={onInspectStudent}
                    />
                  ))}
                </Flex>
              </>
            ) : (
              <Empty description={completedResult.statement} />
            )}

            <Disclosure className="ai-foldout ai-technical" label={<>运行记录</>}>
              <dl>
                <dt>执行来源</dt>
                <dd>{executionOriginText(task.execution_origin)}</dd>
                <dt>工具核查</dt>
                <dd>{task.tool_attempts} 次</dd>
                <dt>模型请求</dt>
                <dd>{task.model_requests} 次</dd>
              </dl>
            </Disclosure>
          </>
        )}
      </Flex>
    </Panel>
  );
}

function FindingCard({
  finding,
  index,
  defaultExpanded,
  studentByKey,
  onInspectStudent,
}: {
  finding: ProgrammingFinding;
  index: number;
  defaultExpanded: boolean;
  studentByKey: Map<string, ProgrammingStudentRef>;
  onInspectStudent: (studentPk: string, focusProblem?: string) => void;
}) {
  const problems = Array.from(new Set(finding.evidence.map((item) => item.problem_label)));
  const testLabels = Array.from(
    new Set(finding.evidence.map((item) => formatTestTag(item.test_index))),
  );
  const titleText = problems.length ? problems.join('、') : `发现 ${index + 1}`;

  return (
    <Card
      size="small"
      type="inner"
      className="programming-finding-card"
      data-testid="programming-finding-card"
      title={titleText}
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

        {finding.evidence.length > 0 && (
          <Disclosure
            defaultExpanded={defaultExpanded}
            label={<>查看证据明细（{finding.evidence.length} 条）</>}
          >
            <Table<ProgrammingEvidence & { rowKey: string }>
              rowKey="rowKey"
              dataSource={finding.evidence.map((item, evidenceIndex) => ({
                ...item,
                rowKey: `${finding.finding_id}:${item.asset_id}:${item.student_key}:${item.problem_label}:${item.test_index ?? 'v'}:${evidenceIndex}`,
              }))}
              scroll={{ x: 'max-content' }}
              pagination={{ pageSize: 5, hideOnSinglePage: true, showSizeChanger: false }}
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
                  title: '题目',
                  dataIndex: 'problem_label',
                },
                {
                  title: '定位',
                  render: (_, item) => evidenceLocationText(item),
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
