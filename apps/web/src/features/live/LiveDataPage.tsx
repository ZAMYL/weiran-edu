'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Button,
  Card,
  Drawer,
  Dropdown,
  Grid,
  Flex,
  Modal,
  Segmented,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  EllipsisOutlined,
  FileExcelTwoTone,
  UploadOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { AssessmentContext, Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import type { ApiBatch } from '../../services/edu-api/types';
import { dimensionName } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import LiveImportPreview from './LiveImportPreview';
import { assessmentNameFromFilename } from '../../services/assessment-name';
import UploadWorkflow from './UploadWorkflow';
import AssessmentItemPage from './AssessmentItemPage';
import QuestionImportPreview from './QuestionImportPreview';
import { isQuestionPreview } from '../../services/edu-api/question-types';

export default function LiveDataPage({ view = 'upload' }: { view?: 'upload' | 'history' }) {
  const {
    client,
    scopes,
    batch,
    notice,
    selectBatch,
    revision,
    batchListRevision,
    batchArchiveChanged,
    assessmentTitle,
    uploadDraft,
    setUploadDraft,
  } = useDataMode();
  const router = useRouter();
  const screens = Grid.useBreakpoint();
  const importScopes = scopes.filter((s) => s.can_import);
  const draft = uploadDraft ?? {
    course: '',
    date: '',
    scope:
      importScopes.find((scope) => scope.data_scope_id === batch?.data_scope_id)?.data_scope_id ??
      (importScopes.length === 1 ? importScopes[0]!.data_scope_id : ''),
    preview: null,
    file: null,
    title: '',
    committed: null,
  };
  const { scope, preview, file: selectedFile } = draft;
  const setScope = (scope: string) => setUploadDraft({ ...draft, scope });
  const [offset, setOffset] = useState(0);
  const [archived, setArchived] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<ApiBatch | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveError, setArchiveError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [qualityBatch, setQualityBatch] = useState<ApiBatch | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      if (uploadController.current) {
        uploadController.current.abort();
        uploadController.current = null;
        setBusy(false);
      }
    },
    [],
  );
  const loadBatches = useCallback(
    (signal: AbortSignal) => client.batches(offset, 10, signal, archived),
    [client, offset, archived],
  );
  const batches = useRemote(
    view === 'history'
      ? `data-batches:${revision}:${batchListRevision}:${offset}:${archived}`
      : null,
    loadBatches,
  );
  const allowed = scopes.some((s) => s.data_scope_id === scope && s.can_import);
  const firstUpload = view === 'upload' && !batch && !notice && !selectedFile;
  function selectFile(file: File) {
    if (busy || uploadController.current) return Upload.LIST_IGNORE;
    setError(null);
    setLocalError(null);
    if (!allowed) {
      setLocalError('请选择当前有导入权的数据来源。');
      return Upload.LIST_IGNORE;
    }
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setLocalError('目前只支持 .xlsx 测评文件，请重新选择。');
      return Upload.LIST_IGNORE;
    }
    if (file.size > 10 * 1024 * 1024) {
      setLocalError('文件超过 10 MB，请缩小后重新上传。');
      return Upload.LIST_IGNORE;
    }
    const selected = {
      ...draft,
      file,
      title: assessmentNameFromFilename(file.name),
      preview: null,
      committed: null,
    };
    setUploadDraft(selected);
    void checkFile(file, selected);
    return Upload.LIST_IGNORE;
  }
  async function checkFile(file: File, selected: typeof draft) {
    if (uploadController.current || !allowed) return;
    const controller = new AbortController();
    uploadController.current = controller;
    setBusy(true);
    setError(null);
    setLocalError(null);
    const form = new FormData();
    form.append('file', file);
    form.append('data_scope_id', selected.scope);
    if (selected.title.trim()) form.append('title', selected.title.trim());
    if (selected.course.trim()) form.append('course', selected.course.trim());
    if (selected.date) form.append('assessment_date', selected.date);
    try {
      const result = await client.preview(form, controller.signal);
      if (!controller.signal.aborted)
        setUploadDraft({ ...selected, preview: result, committed: null });
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setError(e);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        uploadController.current = null;
      }
    }
  }
  const enter = (target: ApiBatch) => {
    selectBatch(target);
    router.push('/insights');
  };
  async function changeArchive(target: ApiBatch) {
    if (archiveBusy) return;
    setArchiveBusy(true);
    setArchiveError(null);
    try {
      const result = await client.setBatchArchive(target, !archived);
      if (result.archived === archived) throw new ApiError(502, 'INVALID_API_RESPONSE');
      setArchiveTarget(null);
      if (offset && batches.data?.items.length === 1) setOffset(offset - 10);
      batchArchiveChanged(target.batch_id, result.archived);
    } catch (cause) {
      if (cause instanceof ApiError) setArchiveError(cause);
    } finally {
      setArchiveBusy(false);
    }
  }
  if (view === 'upload' && preview) {
    return isQuestionPreview(preview) ? (
      <QuestionImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />
    ) : (
      <LiveImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />
    );
  }
  return (
    <div
      className={`page-stack data-page live-data${firstUpload ? ' first-assessment-upload' : ''}`}
    >
      {view === 'upload' && (
        <UploadWorkflow step={busy ? 1 : 0} busy={busy}>
          {importScopes.length > 1 && (
            <div className="live-upload-fields">
              <label htmlFor="live-scope">数据来源</label>
              <Select
                id="live-scope"
                aria-label="授权数据来源"
                value={scope || undefined}
                options={importScopes.map((s) => ({ value: s.data_scope_id, label: s.label }))}
                onChange={setScope}
                disabled={busy}
                placeholder="选择具有导入权的数据来源"
              />
            </div>
          )}
          {!importScopes.length && (
            <Alert type="warning" showIcon title="当前教师没有导入权，可查看已授权批次。" />
          )}
          <div className="workflow-upload-target" hidden={Boolean(selectedFile) || busy}>
            <Upload.Dragger
              accept=".xlsx"
              multiple={false}
              showUploadList={false}
              beforeUpload={selectFile}
              disabled={busy || !allowed}
              className="excel-dropzone workflow-dropzone"
            >
              <div className="assessment-file-prompt">
                <UploadOutlined className="assessment-file-symbol" aria-hidden="true" />
                <Button type="primary" tabIndex={-1} disabled={!allowed}>
                  选择文件
                </Button>
                <p>选择或拖入 .xlsx 测评表（最大 10 MB），系统会先检查评分数据。</p>
              </div>
            </Upload.Dragger>
          </div>
          {selectedFile && (
            <>
              <Card size="small" type="inner" className="selected-assessment-file">
                <Flex align="center" gap={tokens.sizeSM}>
                  <FileExcelTwoTone
                    twoToneColor={tokens.colorSuccess}
                    style={{ fontSize: 28, flex: 'none' }}
                    aria-hidden="true"
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Typography.Title level={5} style={{ margin: 0, overflowWrap: 'anywhere' }}>
                      {selectedFile.name}
                    </Typography.Title>
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      {selectedFile.size < 1024 * 1024
                        ? `${Math.max(1, Math.round(selectedFile.size / 1024))} KiB`
                        : `${(selectedFile.size / 1024 / 1024).toFixed(1)} MiB`}
                    </Typography.Text>
                  </div>
                  <Button disabled={busy || !allowed} onClick={() => setUploadDraft(null)}>
                    更换文件
                  </Button>
                </Flex>
              </Card>
              {busy ? (
                <div className="workflow-checking" role="status">
                  <Spin />
                  <Typography.Title level={4} style={{ margin: 0 }}>
                    正在检查文件
                  </Typography.Title>
                </div>
              ) : (
                <div>
                  <Button onClick={() => void checkFile(selectedFile, draft)} disabled={!allowed}>
                    重新检查
                  </Button>
                </div>
              )}
            </>
          )}
          {localError && (
            <Alert className="session-notice" type="error" showIcon title={localError} />
          )}
          {error && <RequestError error={error} />}
        </UploadWorkflow>
      )}
      {view === 'history' && (
        <Panel className="batches-panel">
          <Flex vertical gap={tokens.margin}>
            <Flex
              className="history-status-filter"
              align="center"
              justify="space-between"
              gap={tokens.marginXS}
              wrap
            >
              <Segmented
                aria-label="历史测评状态"
                value={archived ? 'archived' : 'active'}
                options={[
                  { value: 'active', label: '使用中' },
                  { value: 'archived', label: '已归档' },
                ]}
                onChange={(value) => {
                  setOffset(0);
                  setArchived(value === 'archived');
                }}
              />
              <Button icon={<ReloadOutlined />} onClick={batches.retry}>
                刷新列表
              </Button>
            </Flex>
            {batches.loading && <LoadingPanel />}
            {batches.error && <RequestError error={batches.error} retry={batches.retry} />}
            {batches.data && (
              <Table<ApiBatch>
                className="history-assessment-table"
                rowKey="batch_id"
                rowClassName="history-assessment-row"
                dataSource={batches.data.items}
                pagination={{
                  current: Math.floor(offset / 10) + 1,
                  total: batches.data.total_count,
                  pageSize: 10,
                  responsive: true,
                  showSizeChanger: false,
                  onChange: (page) => setOffset((page - 1) * 10),
                  showTotal: (total) => `共 ${total} 份测评`,
                }}
                columns={[
                  {
                    title: '测评名称',
                    render: (_, target) => (
                      <Flex vertical gap={tokens.marginXXS}>
                        <Space wrap size="small">
                          <Typography.Text strong style={{ overflowWrap: 'anywhere' }}>
                            {assessmentTitle(target)}
                          </Typography.Text>
                          {target.batch_id === batch?.batch_id && <Tag color="cyan">当前测评</Tag>}
                        </Space>
                        {!screens.lg && (
                          <Typography.Text type="secondary">
                            {target.student_count} 人
                            {target.evaluation_count != null &&
                              ` · ${target.evaluation_count} 条评分`}
                          </Typography.Text>
                        )}
                      </Flex>
                    ),
                  },
                  {
                    title: '参评人数',
                    responsive: ['lg'],
                    dataIndex: 'student_count',
                  },
                  {
                    title: '评分记录',
                    responsive: ['lg'],
                    dataIndex: 'evaluation_count',
                  },
                  {
                    title: '操作',
                    className: 'history-actions-cell',
                    render: (_, target) => {
                      const editArchive = () => {
                        setArchiveError(null);
                        setArchiveTarget(target);
                      };
                      if (archived) {
                        return (
                          <Button type="link" size="small" onClick={editArchive}>
                            恢复
                          </Button>
                        );
                      }
                      return (
                        <Space size="small" className="batch-actions">
                          <Button type="link" size="small" onClick={() => enter(target)}>
                            查看学情
                          </Button>
                          {screens.lg ? (
                            <>
                              <Button
                                type="link"
                                size="small"
                                onClick={() => setQualityBatch(target)}
                              >
                                评分概况
                              </Button>
                              <Button type="link" size="small" danger onClick={editArchive}>
                                删除
                              </Button>
                            </>
                          ) : (
                            <Dropdown
                              trigger={['click']}
                              menu={{
                                items: [
                                  {
                                    key: 'overview',
                                    label: '评分概况',
                                    onClick: () => setQualityBatch(target),
                                  },
                                  {
                                    key: 'archive',
                                    label: '删除',
                                    danger: true,
                                    onClick: editArchive,
                                  },
                                ],
                              }}
                            >
                              <Button
                                type="text"
                                size="small"
                                aria-label="更多操作"
                                icon={<EllipsisOutlined />}
                              />
                            </Dropdown>
                          )}
                        </Space>
                      );
                    },
                  },
                ]}
              />
            )}
          </Flex>
        </Panel>
      )}
      {archiveTarget && (
        <Modal
          open
          title={archived ? '恢复测评' : '从历史测评删除'}
          okText={archived ? '恢复' : '归档'}
          cancelText="取消"
          confirmLoading={archiveBusy}
          closable={!archiveBusy}
          mask={{ closable: !archiveBusy }}
          cancelButtonProps={{ disabled: archiveBusy }}
          onCancel={() => {
            if (!archiveBusy) setArchiveTarget(null);
          }}
          onOk={() => void changeArchive(archiveTarget)}
        >
          <p>{assessmentTitle(archiveTarget)}</p>
          {!archived && <p>归档仅影响当前教师账户，可从「已归档」恢复。原始评分与报告会保留。</p>}
          {archiveError && <RequestError error={archiveError} />}
        </Modal>
      )}
      {qualityBatch && <BatchOverview batch={qualityBatch} onClose={() => setQualityBatch(null)} />}
    </div>
  );
}
function BatchOverview({ batch, onClose }: { batch: ApiBatch; onClose: () => void }) {
  const { client } = useDataMode();
  const load = useCallback(
    (signal: AbortSignal) => client.statistics(batch, undefined, undefined, signal),
    [client, batch],
  );
  const resource = useRemote(
    batch.recognized_items ? null : `batch-overview:${batch.batch_id}:${batch.snapshot_id}`,
    load,
  );
  return (
    <Drawer title="测评评分概况" open onClose={onClose} size="min(550px, 100vw)">
      <AssessmentContext title={batch.title} course={batch.course} date={batch.assessment_date} />
      {batch.recognized_items && <AssessmentItemPage batch={batch} view="overview" />}
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && (
        <Panel title="评分完整性">
          <Flex vertical gap={tokens.sizeSM}>
            <Table
              rowKey="dimension_code"
              showHeader={false}
              pagination={false}
              dataSource={resource.data.dimensions}
              columns={[
                {
                  title: '维度',
                  render: (_, d) => dimensionName(d.dimension_code),
                },
                {
                  title: '统计',
                  render: (_, d) => (
                    <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                      有效 {d.valid_count} · 缺失 {d.missing_count} · 无效 {d.invalid_count}
                    </Typography.Text>
                  ),
                },
              ]}
            />
            <Disclosure label={<>评分标准版本</>}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {batch.rubric_version}
              </Typography.Text>
            </Disclosure>
          </Flex>
        </Panel>
      )}
    </Drawer>
  );
}
