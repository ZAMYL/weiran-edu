'use client';
import { tokens } from '../../app/theme';

import { Disclosure } from '../../components/Disclosure';
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Button,
  Card,
  Col,
  Drawer,
  Empty,
  Flex,
  Form,
  Pagination,
  Row,
  Select,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { FileZipTwoTone, FolderOpenOutlined, UploadOutlined } from '@ant-design/icons';
import { answerHistory as library } from '../../app/workspace-routes';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import type { answerSheetDto } from '../../services/edu-api/answer-sheets';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';
import UploadWorkflow from './UploadWorkflow';
import ProgrammingAiPanel from './ProgrammingAiPanel';

const maxFiles = 20;
const maxBytes = 10 * 1024 * 1024;

export function AnswerSheetUpload() {
  const router = useRouter();
  const { client, scopes } = useDataMode();
  const [files, setFiles] = useState<File[]>([]);
  const [scope, setScope] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError>();
  const upload = useRef<AbortController | null>(null);
  const importScopes = scopes.filter((item) => item.can_import);
  const selectedScope = importScopes.length === 1 ? importScopes[0]?.data_scope_id : scope;
  const invalid =
    files.length > maxFiles ||
    files.some((file) => !/\.zip$/i.test(file.name)) ||
    files.reduce((total, file) => total + file.size, 0) > maxBytes;
  useEffect(
    () => () => {
      if (upload.current) {
        upload.current.abort();
        upload.current = null;
        setBusy(false);
      }
    },
    [],
  );

  async function submit() {
    if (upload.current || invalid || !files.length || !selectedScope) return;
    const controller = new AbortController();
    upload.current = controller;
    setBusy(true);
    setError(undefined);
    const form = new FormData();
    for (const file of files) form.append('file', file);
    form.append('data_scope_id', selectedScope);
    try {
      const result = await client.uploadAnswerSheets(form, controller.signal);
      router.push(`${library}/${encodeURIComponent(result.assessment_id)}`);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof ApiError ? cause : new ApiError(502, 'INVALID_API_RESPONSE'));
    } finally {
      if (!controller.signal.aborted) {
        upload.current = null;
        setBusy(false);
      }
    }
  }

  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  const sizeLabel =
    totalBytes < 1024 * 1024
      ? `${Math.max(1, Math.round(totalBytes / 1024))} KiB`
      : `${(totalBytes / 1024 / 1024).toFixed(1)} MiB`;

  return (
    <div className="page-stack data-page live-data">
      <Form onFinish={submit}>
        <UploadWorkflow
          step={busy ? 1 : 0}
          busy={busy}
          steps={['选择 ZIP 文件', '解析并查看']}
          actions={
            files.length > 0 ? (
              <>
                <Button
                  disabled={busy}
                  onClick={() => {
                    setFiles([]);
                    setError(undefined);
                  }}
                >
                  重新选择
                </Button>
                <div className="workflow-next-action">
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={busy}
                    disabled={!selectedScope || !files.length || invalid}
                  >
                    {busy ? '正在导入' : '上传并查看结果'}
                  </Button>
                </div>
              </>
            ) : undefined
          }
        >
          {importScopes.length > 1 && (
            <div className="live-upload-fields">
              <label htmlFor="answer-sheet-scope">数据来源</label>
              <Select
                id="answer-sheet-scope"
                aria-label="授权数据来源"
                placeholder="选择数据来源"
                value={scope}
                disabled={busy}
                onChange={setScope}
                options={importScopes.map((item) => ({
                  value: item.data_scope_id,
                  label: item.label,
                }))}
              />
            </div>
          )}
          {!importScopes.length && <Alert type="warning" showIcon title="当前账户没有上传权限" />}
          <div className="workflow-upload-target" hidden={files.length > 0 || busy}>
            <Upload.Dragger
              accept=".zip"
              multiple
              showUploadList={false}
              disabled={busy || !importScopes.length}
              className="excel-dropzone workflow-dropzone"
              beforeUpload={(file, selection) => {
                if (file.uid === selection[0]?.uid) {
                  setFiles(selection);
                  setError(undefined);
                }
                return Upload.LIST_IGNORE;
              }}
            >
              <div className="assessment-file-prompt">
                <UploadOutlined className="assessment-file-symbol" aria-hidden="true" />
                <Button type="primary" tabIndex={-1} disabled={busy || !importScopes.length}>
                  选择 ZIP 文件
                </Button>
                <p>最多 20 个 ZIP，总大小不超过 10 MB。</p>
              </div>
            </Upload.Dragger>
          </div>
          {files.length > 0 && (
            <>
              <Card size="small" type="inner" className="selected-assessment-file">
                <Flex align="center" gap={tokens.sizeSM}>
                  <FileZipTwoTone
                    twoToneColor={tokens.colorPrimary}
                    style={{ fontSize: 28, flex: 'none' }}
                    aria-hidden="true"
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Typography.Title level={5} style={{ margin: 0, overflowWrap: 'anywhere' }}>
                      {files.length === 1 ? files[0]?.name : `已选 ${files.length} 个文件`}
                    </Typography.Title>
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      {files.length === 1 ? sizeLabel : `${files.length} 个 ZIP · 共 ${sizeLabel}`}
                    </Typography.Text>
                  </div>
                  <Button
                    disabled={busy}
                    onClick={() => {
                      setFiles([]);
                      setError(undefined);
                    }}
                  >
                    更换文件
                  </Button>
                </Flex>
              </Card>
              {busy && (
                <div className="workflow-checking" role="status">
                  <Spin />
                  <Typography.Title level={4} style={{ margin: 0 }}>
                    正在导入答题卡
                  </Typography.Title>
                </div>
              )}
            </>
          )}
          {invalid && <Alert type="error" showIcon title="请选择符合数量和大小限制的 ZIP 文件" />}
          {error && <RequestError error={error} />}
        </UploadWorkflow>
      </Form>
    </div>
  );
}

export function AnswerSheetLibrary() {
  const { client } = useDataMode();
  const [page, setPage] = useState(1);
  const load = useCallback(
    (signal: AbortSignal) => client.answerSheets((page - 1) * 20, signal),
    [client, page],
  );
  const history = useRemote(String(page), load);
  return (
    <div className="page-stack">
      <Panel>
        {history.loading && <LoadingPanel />}
        {history.error && <RequestError error={history.error} retry={history.retry} />}
        {history.data &&
          (history.data.items.length ? (
            <>
              <Table
                rowKey="assessment_id"
                pagination={false}
                dataSource={history.data.items}
                columns={[
                  {
                    title: '测评名称',
                    render: (_, item) => (
                      <Link href={`${library}/${encodeURIComponent(item.assessment_id)}`}>
                        {item.title}
                      </Link>
                    ),
                  },
                  { title: '人数', dataIndex: 'student_count' },
                  { title: '题数', dataIndex: 'problem_count' },
                  {
                    title: '状态',
                    render: (_, item) =>
                      item.review_state === 'needs_review' ? (
                        <Tag color="warning">待核查</Tag>
                      ) : (
                        <Tag color="success">可查看</Tag>
                      ),
                  },
                ]}
              />
              <Pagination
                current={page}
                pageSize={20}
                total={history.data.total}
                onChange={setPage}
                showSizeChanger={false}
                hideOnSinglePage
              />
            </>
          ) : (
            <Empty description="暂无答题卡测评" />
          ))}
      </Panel>
    </div>
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
  const assets = new Map(overview.assets.map((asset) => [asset.asset_id, asset.entry]));
  const diagnostics = overview.diagnostics.filter((item) => item.code !== 'TEST_COLUMNS_INFERRED');
  const pagination = { hideOnSinglePage: true, showSizeChanger: false };
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
                render: (_, item) =>
                  item.entry ? `${item.entry} · ${item.message}` : item.message,
              },
            ]}
          />
        )}
        {!overview.assets.length && !overview.materials.length && !diagnostics.length && (
          <Empty description="暂无资料来源" />
        )}
      </Flex>
    </Drawer>
  );
}

export function AnswerSheetDetails({
  assessmentId,
  onTitleChange,
}: {
  assessmentId: string;
  onTitleChange: (title: string) => void;
}) {
  const { client } = useDataMode();
  const [selection, setSelection] = useState<{
    assessmentId: string;
    studentId: string | null;
    focusProblem: string | null;
  }>({
    assessmentId,
    studentId: null,
    focusProblem: null,
  });
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const sourcesTrigger = useRef<HTMLButtonElement>(null);
  const focusedPanel = useRef<HTMLDivElement>(null);
  const studentId = selection.assessmentId === assessmentId ? selection.studentId : null;
  const focusProblem = selection.assessmentId === assessmentId ? selection.focusProblem : null;
  const openStudentEvidence = useCallback(
    (pk: string, problemLabel?: string) => {
      setSelection({
        assessmentId,
        studentId: pk,
        focusProblem: problemLabel ?? null,
      });
    },
    [assessmentId],
  );
  const closeStudentEvidence = useCallback(() => {
    setSelection({ assessmentId, studentId: null, focusProblem: null });
  }, [assessmentId]);
  const load = useCallback(
    (signal: AbortSignal) => client.answerSheet(assessmentId, signal),
    [client, assessmentId],
  );
  const result = useRemote(assessmentId, load);
  const snapshot = result.data?.overview.source_snapshot_id ?? null;
  const loadEvidence = useCallback(
    (signal: AbortSignal) => client.answerSheetEvidence(assessmentId, studentId!, snapshot, signal),
    [client, assessmentId, studentId, snapshot],
  );
  const evidence = useRemote(result.data ? studentId : null, loadEvidence);
  const stale =
    evidence.error?.code === 'SOURCE_CHANGED' || evidence.error?.code === 'ORIGINAL_UNAVAILABLE';
  const data = result.data;
  const title = data?.overview.title;
  useEffect(() => {
    if (title) onTitleChange(title);
  }, [title, onTitleChange]);
  useEffect(() => {
    if (evidence.data && focusProblem) {
      focusedPanel.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [evidence.data, focusProblem]);
  const selectedStudent = data?.students.find((item) => item.student_pk === studentId);
  const attemptTimes = [
    selectedStudent?.started_at && `开始 ${selectedStudent.started_at}`,
    selectedStudent?.finished_at && `结束 ${selectedStudent.finished_at}`,
  ].filter(Boolean);

  return (
    <div className="page-stack">
      {result.loading && <LoadingPanel />}
      {result.error && <RequestError error={result.error} retry={result.retry} />}
      {data && (
        <>
          <Panel title={data.overview.title}>
            <Flex vertical gap={tokens.size}>
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
              <Button
                ref={sourcesTrigger}
                icon={<FolderOpenOutlined aria-hidden="true" />}
                aria-haspopup="dialog"
                style={{ alignSelf: 'flex-start' }}
                onClick={() => setSourcesOpen(true)}
              >
                查看资料来源
              </Button>
            </Flex>
          </Panel>
          <ProgrammingAiPanel
            key={`${assessmentId}:${snapshot ?? 'none'}`}
            assessmentId={assessmentId}
            sourceSnapshotId={snapshot}
            students={data.students}
            onInspectStudent={openStudentEvidence}
            onRefreshAssessment={result.retry}
          />
          <Panel title="群体错误统计">
            {data.patterns.length ? (
              <div className="answer-sheet-patterns">
                {data.patterns.map((item) => (
                  <Card size="small" type="inner" key={item.ordinal} title={item.problem_label}>
                    <Flex vertical gap={tokens.sizeXS}>
                      <Typography.Text>
                        {item.submission_count} 份提交
                        {item.failure_rate !== null &&
                          ` · 有明确结论的测试点失败率 ${(item.failure_rate * 100).toFixed(1)}%`}
                      </Typography.Text>
                      {item.verdict_counts.length > 0 && (
                        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                          判题分布：
                          {item.verdict_counts
                            .map((entry) => `${entry.status || '未知'} ${entry.count}`)
                            .join('、')}
                        </Typography.Text>
                      )}
                      <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                        {item.worst_test_points.length
                          ? `失败测试点：${item.worst_test_points.map((point) => `第 ${point.test_index} 点（${point.failure_count} 次）`).join('、')}`
                          : '暂无明确失败的测试点'}
                      </Typography.Text>
                    </Flex>
                  </Card>
                ))}
              </div>
            ) : (
              <Empty description="暂无题目统计" />
            )}
          </Panel>
          <Panel title="参与者">
            <Table
              rowKey="student_pk"
              dataSource={data.students}
              locale={{
                emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无参与者" />,
              }}
              pagination={{ pageSize: 10, hideOnSinglePage: true, showSizeChanger: false }}
              columns={[
                {
                  title: '参与者',
                  render: (_, student) => (
                    <Flex vertical gap={tokens.marginXXS}>
                      <Typography.Text strong>
                        {student.display_name || student.external_key || '未命名参与者'}
                      </Typography.Text>
                      {student.display_name && student.external_key && (
                        <Typography.Text type="secondary">{student.external_key}</Typography.Text>
                      )}
                    </Flex>
                  ),
                },
                {
                  title: '操作',
                  render: (_, student) => (
                    <Button
                      type="link"
                      size="small"
                      onClick={() => openStudentEvidence(student.student_pk)}
                    >
                      查看个人证据
                    </Button>
                  ),
                },
              ]}
            />
          </Panel>
          <AnswerSheetSources
            overview={data.overview}
            open={sourcesOpen}
            onClose={() => {
              setSourcesOpen(false);
              requestAnimationFrame(() => sourcesTrigger.current?.focus({ preventScroll: true }));
            }}
          />
        </>
      )}
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
                    result.retry();
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
                          data?.overview.assets.find(
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
    </div>
  );
}
