'use client';
import { tokens } from '../../app/theme';

import { Disclosure } from '../../components/Disclosure';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Flex,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import { FolderOpenOutlined, ReloadOutlined } from '@ant-design/icons';
import { answerAnalysisPath, answerHistory } from '../../app/workspace-routes';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import type { answerSheetDto } from '../../services/edu-api/answer-sheets';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';
import {
  AiCheckView,
  ProblemAnalysisView,
  StudentAnalysisView,
  type AnswerSheetPattern,
  type AnswerSheetStudentRow,
  type StudentDrillFilter,
} from './ProgrammingAiPanel';
import { useProgrammingAiTask } from './useProgrammingAiTask';

export function AnswerSheetLibrary() {
  const router = useRouter();
  const { client } = useDataMode();
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<'all' | 'ready' | 'needs_review'>('all');
  const [sourcesId, setSourcesId] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) => client.answerSheets((page - 1) * 20, signal),
    [client, page],
  );
  const history = useRemote(String(page), load);
  const loadSources = useCallback(
    (signal: AbortSignal) => client.answerSheetOverview(sourcesId!, signal),
    [client, sourcesId],
  );
  const sources = useRemote(sourcesId, loadSources);

  const items = history.data?.items ?? [];
  const filteredItems =
    filter === 'all' ? items : items.filter((item) => item.review_state === filter);

  return (
    <div className="page-stack">
      <Panel className="batches-panel">
        <Flex vertical gap={tokens.margin}>
          <Flex
            className="history-status-filter"
            align="center"
            justify="space-between"
            gap={tokens.marginXS}
            wrap
          >
            <Segmented<'all' | 'ready' | 'needs_review'>
              aria-label="历史答题卡状态"
              value={filter}
              options={[
                { value: 'all', label: '全部' },
                { value: 'ready', label: '可查看' },
                { value: 'needs_review', label: '待核查' },
              ]}
              onChange={setFilter}
            />
            <Button icon={<ReloadOutlined />} onClick={history.retry}>
              刷新列表
            </Button>
          </Flex>
          {history.loading && <LoadingPanel />}
          {history.error && <RequestError error={history.error} retry={history.retry} />}
          {history.data &&
            (items.length ? (
              <Table
                className="history-assessment-table"
                rowKey="assessment_id"
                rowClassName="history-assessment-row"
                scroll={{ x: 'max-content' }}
                pagination={{
                  current: page,
                  pageSize: 20,
                  total: history.data.total,
                  responsive: true,
                  showSizeChanger: false,
                  onChange: setPage,
                  showTotal: (total) => `共 ${total} 份答题卡`,
                }}
                dataSource={filteredItems}
                columns={[
                  {
                    title: '测评名称',
                    render: (_, item) => (
                      <Typography.Text strong style={{ overflowWrap: 'anywhere' }}>
                        {item.title}
                      </Typography.Text>
                    ),
                  },
                  { title: '参评人数', dataIndex: 'student_count' },
                  { title: '题目数', dataIndex: 'problem_count' },
                  {
                    title: '状态',
                    render: (_, item) =>
                      item.review_state === 'needs_review' ? (
                        <Tag color="warning">待核查</Tag>
                      ) : (
                        <Tag color="success">可查看</Tag>
                      ),
                  },
                  {
                    title: '操作',
                    className: 'history-actions-cell',
                    render: (_, item) => (
                      <Space size="small" className="batch-actions">
                        <Button
                          type="link"
                          size="small"
                          onClick={() => router.push(answerAnalysisPath(item.assessment_id))}
                        >
                          查看分析
                        </Button>
                        <Button
                          type="link"
                          size="small"
                          onClick={() => setSourcesId(item.assessment_id)}
                        >
                          资料来源
                        </Button>
                      </Space>
                    ),
                  },
                ]}
              />
            ) : (
              <Empty description="暂无答题卡测评" />
            ))}
        </Flex>
      </Panel>

      <Drawer
        title={sources.data ? `资料来源 · ${sources.data.title}` : '资料来源'}
        open={sourcesId !== null}
        onClose={() => setSourcesId(null)}
        placement="right"
        size="min(650px, 100vw)"
        destroyOnHidden
        focusable={{ trap: true, focusTriggerAfterClose: false }}
        styles={{ body: { overflowWrap: 'anywhere' } }}
      >
        {sources.loading && <LoadingPanel />}
        {sources.error && <RequestError error={sources.error} retry={sources.retry} />}
        {sources.data && <AnswerSheetSourcesContent overview={sources.data} />}
      </Drawer>
    </div>
  );
}

function AnswerSheetSourcesContent({ overview }: { overview: ReturnType<typeof answerSheetDto> }) {
  const assets = new Map(overview.assets.map((asset) => [asset.asset_id, asset.entry]));
  const diagnostics = overview.diagnostics.filter((item) => item.code !== 'TEST_COLUMNS_INFERRED');
  const pagination = { hideOnSinglePage: true, showSizeChanger: false };
  return (
    <Flex vertical gap={tokens.sizeLG}>
      {overview.assets.length > 0 && (
        <Table
          rowKey="asset_id"
          dataSource={overview.assets}
          pagination={pagination}
          columns={[
            {
              title: '来源文件',
              render: (_, asset) =>
                asset.entry + (asset.storage_state !== 'stored' ? '（原件不可用）' : ''),
            },
          ]}
        />
      )}
      {overview.materials.length > 0 && (
        <Table
          dataSource={overview.materials.map((material, key) => ({ ...material, key }))}
          pagination={pagination}
          columns={[
            {
              title: '材料',
              render: (_, material) =>
                material.entry + (material.needs_confirmation ? '（待核查）' : ''),
            },
            {
              title: '来源文件',
              render: (_, material) => assets.get(material.asset_id ?? '') ?? '来源包未确定',
            },
          ]}
        />
      )}
      {diagnostics.length > 0 && (
        <Table
          dataSource={diagnostics.map((item, key) => ({ ...item, key }))}
          pagination={pagination}
          columns={[
            {
              title: '检查结果',
              render: (_, item) => (item.entry ? `${item.entry} · ${item.message}` : item.message),
            },
          ]}
        />
      )}
      {!overview.assets.length && !overview.materials.length && !diagnostics.length && (
        <Empty description="暂无资料来源" />
      )}
    </Flex>
  );
}

function AnswerSheetSources({
  overview,
  open,
  onClose,
}: {
  overview: ReturnType<typeof answerSheetDto>;
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Drawer
      title="资料来源"
      open={open}
      onClose={onClose}
      placement="right"
      size="min(650px, 100vw)"
      destroyOnHidden
      focusable={{ trap: true, focusTriggerAfterClose: false }}
      styles={{ body: { overflowWrap: 'anywhere' } }}
    >
      <AnswerSheetSourcesContent overview={overview} />
    </Drawer>
  );
}

export function AnswerSheetAnalysisEntry() {
  const router = useRouter();
  const { client } = useDataMode();
  const load = useCallback((signal: AbortSignal) => client.answerSheets(0, signal), [client]);
  const list = useRemote('answer-sheet-analysis-entry', load);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const noop = useCallback(() => {}, []);

  if (list.loading) return <LoadingPanel />;
  if (list.error) return <RequestError error={list.error} retry={list.retry} />;

  const items = list.data?.items ?? [];
  if (!items.length) {
    return (
      <Panel>
        <Empty description="暂无可分析的编程答题卡" />
      </Panel>
    );
  }

  const activeId = items.some((item) => item.assessment_id === selectedId)
    ? selectedId!
    : items[0]!.assessment_id;

  return (
    <div className="page-stack">
      <Flex align="center" justify="space-between" wrap gap={tokens.sizeSM}>
        <Flex align="center" gap={tokens.sizeXS} wrap>
          <Typography.Text type="secondary">当前答题卡：</Typography.Text>
          <Select
            aria-label="选择编程答题卡"
            value={activeId}
            onChange={setSelectedId}
            style={{ minWidth: 260 }}
            options={items.map((item) => ({
              value: item.assessment_id,
              label: item.title,
            }))}
          />
        </Flex>
        <Button onClick={() => router.push(answerHistory)}>历史答题卡</Button>
      </Flex>
      <AnswerSheetDetails assessmentId={activeId} onTitleChange={noop} />
    </div>
  );
}

interface AnswerSheetData {
  overview: ReturnType<typeof answerSheetDto>;
  patterns: AnswerSheetPattern[];
  students: AnswerSheetStudentRow[];
}

export function AnswerSheetDetails({
  assessmentId,
  onTitleChange,
}: {
  assessmentId: string;
  onTitleChange: (title: string) => void;
}) {
  const { client } = useDataMode();
  const load = useCallback(
    (signal: AbortSignal) => client.answerSheet(assessmentId, signal),
    [client, assessmentId],
  );
  const result = useRemote(assessmentId, load);
  const data = result.data;
  const title = data?.overview.title;
  useEffect(() => {
    if (title) onTitleChange(title);
  }, [title, onTitleChange]);

  return (
    <div className="page-stack">
      {result.loading && <LoadingPanel />}
      {result.error && <RequestError error={result.error} retry={result.retry} />}
      {data && (
        <AnswerSheetWorkspace
          key={`${assessmentId}:${data.overview.source_snapshot_id ?? 'none'}`}
          assessmentId={assessmentId}
          data={data}
          onRefresh={result.retry}
        />
      )}
    </div>
  );
}

function AnswerSheetWorkspace({
  assessmentId,
  data,
  onRefresh,
}: {
  assessmentId: string;
  data: AnswerSheetData;
  onRefresh: () => void;
}) {
  const { client } = useDataMode();
  const [activeTab, setActiveTab] = useState<'problems' | 'students' | 'ai'>('problems');
  const [drillFilter, setDrillFilter] = useState<StudentDrillFilter | null>(null);
  const [studentId, setStudentId] = useState<string | null>(null);
  const [focusProblem, setFocusProblem] = useState<string | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const sourcesTrigger = useRef<HTMLButtonElement>(null);
  const focusedPanel = useRef<HTMLDivElement>(null);
  const snapshot = data.overview.source_snapshot_id ?? null;

  const ai = useProgrammingAiTask({
    assessmentId,
    sourceSnapshotId: snapshot,
    students: data.students,
  });

  const openStudentEvidence = useCallback((pk: string, problemLabel?: string) => {
    setStudentId(pk);
    setFocusProblem(problemLabel ?? null);
  }, []);
  const closeStudentEvidence = useCallback(() => {
    setStudentId(null);
    setFocusProblem(null);
  }, []);

  const loadEvidence = useCallback(
    (signal: AbortSignal) => client.answerSheetEvidence(assessmentId, studentId!, snapshot, signal),
    [client, assessmentId, studentId, snapshot],
  );
  const evidence = useRemote(studentId, loadEvidence);
  const stale =
    evidence.error?.code === 'SOURCE_CHANGED' || evidence.error?.code === 'ORIGINAL_UNAVAILABLE';

  useEffect(() => {
    if (evidence.data && focusProblem) {
      focusedPanel.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [evidence.data, focusProblem]);

  const selectedStudent = data.students.find((item) => item.student_pk === studentId);
  const attemptTimes = [
    selectedStudent?.started_at && `开始 ${selectedStudent.started_at}`,
    selectedStudent?.finished_at && `结束 ${selectedStudent.finished_at}`,
  ].filter(Boolean);

  const problemCount = data.patterns.length || data.overview.problem_count;
  const focusCount = ai.summary?.students.length ?? 0;
  const aiTabLabel = ai.running
    ? 'AI 核查 (进行中)'
    : ai.completedResult
      ? `AI 核查 (${ai.completedResult.findings.length})`
      : 'AI 核查';

  return (
    <>
      <Panel
        title={data.overview.title}
        action={
          <Button
            ref={sourcesTrigger}
            icon={<FolderOpenOutlined aria-hidden="true" />}
            aria-haspopup="dialog"
            onClick={() => setSourcesOpen(true)}
          >
            查看资料来源
          </Button>
        }
      >
        <Flex vertical gap={tokens.sizeSM}>
          {data.overview.counts_provisional && (
            <Alert type="warning" showIcon title="这份测评有待核查项，人数和统计为暂定结果。" />
          )}
          <Row gutter={[tokens.size, tokens.size]} className="answer-sheet-metrics">
            <Col xs={24} sm={8}>
              <Card size="small" type="inner">
                <Statistic title="参与人数" value={data.overview.student_count} />
              </Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card size="small" type="inner">
                <Statistic title="题目" value={data.overview.problem_count} />
              </Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card size="small" type="inner">
                <Statistic title="来源文件" value={data.overview.assets.length} />
              </Card>
            </Col>
          </Row>
        </Flex>
      </Panel>

      <Card
        className="panel programming-ai-panel"
        title={
          <Segmented<'problems' | 'students' | 'ai'>
            aria-label="分析视角"
            style={{ fontWeight: 'normal' }}
            value={activeTab}
            onChange={setActiveTab}
            options={[
              { value: 'problems', label: `题目分析 (${problemCount})` },
              {
                value: 'students',
                label:
                  focusCount > 0
                    ? `学生分析 (${data.students.length} · 关注 ${focusCount})`
                    : `学生分析 (${data.students.length})`,
              },
              { value: 'ai', label: aiTabLabel },
            ]}
          />
        }
      >
        {activeTab === 'problems' ? (
          <ProblemAnalysisView
            patterns={data.patterns}
            onDrillToStudents={(filter) => {
              setDrillFilter(filter);
              setActiveTab('students');
            }}
          />
        ) : activeTab === 'students' ? (
          <StudentAnalysisView
            assessmentId={assessmentId}
            sourceSnapshotId={snapshot}
            patterns={data.patterns}
            students={data.students}
            ai={ai}
            drillFilter={drillFilter}
            onClearDrillFilter={() => setDrillFilter(null)}
            onInspectStudent={openStudentEvidence}
            onRefreshAssessment={onRefresh}
          />
        ) : (
          <AiCheckView
            students={data.students}
            sourceSnapshotId={snapshot}
            ai={ai}
            onInspectStudent={openStudentEvidence}
            onRefreshAssessment={onRefresh}
          />
        )}
      </Card>

      <AnswerSheetSources
        overview={data.overview}
        open={sourcesOpen}
        onClose={() => {
          setSourcesOpen(false);
          requestAnimationFrame(() => sourcesTrigger.current?.focus({ preventScroll: true }));
        }}
      />

      <Drawer
        title="个人答题证据"
        open={studentId !== null}
        onClose={closeStudentEvidence}
        size="min(650px, 100vw)"
        rootClassName="answer-sheet-evidence"
      >
        {evidence.loading && <LoadingPanel />}
        {evidence.error &&
          (stale ? (
            <Alert
              type="warning"
              title={evidence.error.code === 'SOURCE_CHANGED' ? '资料来源已变化' : '原件当前不可用'}
              action={
                <Button
                  onClick={() => {
                    closeStudentEvidence();
                    onRefresh();
                  }}
                >
                  刷新测评
                </Button>
              }
            />
          ) : (
            <RequestError error={evidence.error} retry={evidence.retry} />
          ))}
        {evidence.data && (
          <div className="page-stack">
            <div>
              <h2>{evidence.data.display_name || evidence.data.external_key}</h2>
              {evidence.data.display_name && evidence.data.external_key && (
                <p>{evidence.data.external_key}</p>
              )}
              {attemptTimes.length > 0 && <p>{attemptTimes.join(' · ')}</p>}
            </div>
            {!evidence.data.submissions.length && <Empty description="暂无提交证据" />}
            {evidence.data.submissions.map((submission) => {
              const isFocused = focusProblem === submission.problem_label;
              return (
                <div key={submission.ordinal} ref={isFocused ? focusedPanel : undefined}>
                  <Panel
                    title={submission.problem_label}
                    action={isFocused ? <Tag color="processing">调查关联题目</Tag> : undefined}
                  >
                    {submission.verdict && <p>判题结果：{submission.verdict}</p>}
                    {submission.source?.entry && (
                      <p className="muted">
                        {[
                          data.overview.assets.find(
                            (asset) => asset.asset_id === submission.source?.asset_id,
                          )?.entry,
                          submission.source.entry,
                          submission.source.source_index != null
                            ? `题块 ${submission.source.source_index}`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </p>
                    )}
                    {submission.code_state === 'PRESENT' && submission.code && (
                      <Disclosure
                        defaultExpanded
                        label={
                          <>
                            代码{submission.language && ` · ${submission.language}`}
                            {submission.code_truncated && '（部分）'}
                          </>
                        }
                      >
                        <pre className="answer-sheet-code">
                          <code>{submission.code}</code>
                        </pre>
                      </Disclosure>
                    )}
                    {submission.compiler_output && (
                      <Disclosure
                        label={<>编译输出{submission.compiler_output_truncated && '（部分）'}</>}
                      >
                        <pre className="answer-sheet-code">{submission.compiler_output}</pre>
                      </Disclosure>
                    )}
                    {submission.observations.length > 0 && (
                      <Disclosure defaultExpanded label={<>测试点</>}>
                        <ul>
                          {submission.observations.map((point, index) => (
                            <li key={`${point.test_index}:${point.source_row ?? index}`}>
                              {[
                                point.test_index != null ? `第 ${point.test_index} 点` : null,
                                point.status,
                                point.duration_text,
                                point.memory_text,
                                point.source_row != null ? `原表第 ${point.source_row} 行` : null,
                              ]
                                .filter(Boolean)
                                .join(' · ')}
                            </li>
                          ))}
                        </ul>
                      </Disclosure>
                    )}
                  </Panel>
                </div>
              );
            })}
          </div>
        )}
      </Drawer>
    </>
  );
}
