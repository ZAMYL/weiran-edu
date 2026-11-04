'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Drawer, Select, Table, Tag, Upload, Spin } from 'antd';
import { FileExcelTwoTone, UploadOutlined, ReloadOutlined } from '@ant-design/icons';
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

export default function LiveDataPage({ view = 'upload' }: { view?: 'upload' | 'history' }) {
  const { client, scopes, batch, selectBatch, revision, uploadDraft, setUploadDraft } =
    useDataMode();
  const router = useRouter();
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [qualityBatch, setQualityBatch] = useState<ApiBatch | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  useEffect(() => () => uploadController.current?.abort(), []);
  const loadBatches = useCallback(
    (signal: AbortSignal) => client.batches(offset, 10, signal),
    [client, offset],
  );
  const batches = useRemote(`data-batches:${revision}:${offset}`, loadBatches);
  const allowed = scopes.some((s) => s.data_scope_id === scope && s.can_import);
  function selectFile(file: File) {
    if (busy || uploadController.current) return Upload.LIST_IGNORE;
    setError(null);
    setLocalError(null);
    if (!allowed) {
      setLocalError('请选择当前有导入权的数据来源。');
      return Upload.LIST_IGNORE;
    }
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 10 * 1024 * 1024) {
      setLocalError('请选择不超过 10 MiB 的.xlsx 文件。');
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
  if (view === 'upload' && preview)
    return <LiveImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />;
  return (
    <div className="page-stack data-page live-data">
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
              <UploadOutlined className="assessment-file-symbol" aria-hidden="true" />
              <h2>选择 Excel 文件</h2>
              <p className="workflow-drag-label">拖拽文件到此处</p>
              <Button type="primary" tabIndex={-1} disabled={!allowed}>
                选择文件
              </Button>
              <p>.xlsx · 最大 10 MiB</p>
            </Upload.Dragger>
          </div>
          {selectedFile && (
            <>
              <div className="selected-assessment-file">
                <FileExcelTwoTone twoToneColor="#12805b" aria-hidden="true" />
                <div>
                  <h3>{selectedFile.name}</h3>
                  <p>
                    {selectedFile.size < 1024 * 1024
                      ? `${Math.max(1, Math.round(selectedFile.size / 1024))} KiB`
                      : `${(selectedFile.size / 1024 / 1024).toFixed(1)} MiB`}
                  </p>
                </div>
                <Button disabled={busy || !allowed} onClick={() => setUploadDraft(null)}>
                  更换文件
                </Button>
              </div>
              {busy ? (
                <div className="workflow-checking" role="status">
                  <Spin />
                  <h2>正在检查文件</h2>
                </div>
              ) : (
                <Button onClick={() => void checkFile(selectedFile, draft)} disabled={!allowed}>
                  重新检查
                </Button>
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
        <Panel
          title="历史测评"
          action={
            <Button icon={<ReloadOutlined />} onClick={batches.retry}>
              刷新批次
            </Button>
          }
          className="batches-panel"
        >
          {batches.loading && <LoadingPanel />}
          {batches.error && <RequestError error={batches.error} retry={batches.retry} />}
          {batches.data && (
            <Table<ApiBatch>
              rowKey="batch_id"
              dataSource={batches.data.items}
              scroll={{ x: 780 }}
              size="small"
              pagination={{
                current: Math.floor(offset / 10) + 1,
                total: batches.data.total_count,
                pageSize: 10,
                showSizeChanger: false,
                onChange: (page) => setOffset((page - 1) * 10),
                showTotal: (total) => `共 ${total} 个授权批次`,
              }}
              columns={[
                {
                  title: '测评名称',
                  dataIndex: 'title',
                  render: (value, target) => (
                    <div className="batch-title">
                      <strong>{value}</strong>
                      {target.batch_id === batch?.batch_id && <Tag color="cyan">当前批次</Tag>}
                    </div>
                  ),
                },
                { title: '参评人数', dataIndex: 'student_count' },
                { title: '评分记录', dataIndex: 'evaluation_count' },
                {
                  title: '操作',
                  render: (_, target) => (
                    <div className="batch-actions">
                      <Button onClick={() => setQualityBatch(target)}>评分概况</Button>
                      <Button type="primary" onClick={() => enter(target)}>
                        查看学情
                      </Button>
                    </div>
                  ),
                },
              ]}
            />
          )}
        </Panel>
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
  const resource = useRemote(`batch-overview:${batch.batch_id}:${batch.snapshot_id}`, load);
  return (
    <Drawer title="测评评分概况" open onClose={onClose} size={550}>
      <AssessmentContext title={batch.title} course={batch.course} date={batch.assessment_date} />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && (
        <Panel title="评分完整性">
          {resource.data.dimensions.map((d) => (
            <div className="server-mean-row" key={d.dimension_code}>
              <span>{dimensionName(d.dimension_code)}</span>
              <span>
                有效 {d.valid_count} · 缺失 {d.missing_count} · 无效 {d.invalid_count}
              </span>
            </div>
          ))}
          <details>
            <summary>评分标准版本</summary>
            <p className="muted small">{batch.rubric_version}</p>
          </details>
        </Panel>
      )}
    </Drawer>
  );
}
