'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Collapse, Drawer, Input, Select, Steps, Table, Tag, Upload } from 'antd';
import { CloudUploadOutlined, ReloadOutlined } from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { AssessmentContext, Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import type { ApiBatch, ApiImport } from '../../services/edu-api/types';
import { dimensionName } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import LiveImportPreview from './LiveImportPreview';
import { assessmentNameFromFilename } from '../../services/assessment-name';
import { UploadSelection } from '../assessments/UploadSelection';

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
  const { course, date, scope, preview, file: selectedFile, title } = draft;
  const setCourse = (course: string) => setUploadDraft({ ...draft, course });
  const setDate = (date: string) => setUploadDraft({ ...draft, date });
  const setScope = (scope: string) => setUploadDraft({ ...draft, scope });
  const setTitle = (title: string) => setUploadDraft({ ...draft, title });
  const setPreview = (preview: ApiImport | null) =>
    setUploadDraft({ ...draft, preview, committed: null });
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
    if (busy) return Upload.LIST_IGNORE;
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
    setUploadDraft({
      ...draft,
      file,
      title: assessmentNameFromFilename(file.name),
      preview: null,
      committed: null,
    });
    return Upload.LIST_IGNORE;
  }
  async function next() {
    if (busy || !selectedFile || !allowed) return;
    uploadController.current?.abort();
    const controller = new AbortController();
    uploadController.current = controller;
    setBusy(true);
    setError(null);
    setLocalError(null);
    const form = new FormData();
    form.append('file', selectedFile);
    form.append('data_scope_id', scope);
    if (title.trim()) form.append('title', title.trim());
    if (course.trim()) form.append('course', course.trim());
    if (date) form.append('assessment_date', date);
    try {
      const result = await client.preview(form, controller.signal);
      if (!controller.signal.aborted) setPreview(result);
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setError(e);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  const enter = (target: ApiBatch) => {
    selectBatch(target);
    router.push('/insights');
  };
  if (view === 'upload' && preview)
    return <LiveImportPreview initial={preview} onCancel={() => setPreview(null)} />;
  return (
    <div className="page-stack data-page live-data">
      {view === 'upload' && (
        <div className="data-top-grid">
          <Panel title="上传测评" className="upload-panel">
            <Steps
              current={busy ? 1 : 0}
              size="small"
              responsive={false}
              items={[{ title: '选择文件' }, { title: '自动检查' }, { title: '确认导入' }]}
            />
            {scopes.filter((s) => s.can_import).length > 1 && (
              <div className="live-upload-fields">
                <label htmlFor="live-scope">数据来源</label>
                <Select
                  id="live-scope"
                  aria-label="授权数据来源"
                  value={scope || undefined}
                  options={scopes
                    .filter((s) => s.can_import)
                    .map((s) => ({ value: s.data_scope_id, label: s.label }))}
                  onChange={setScope}
                  disabled={busy}
                  placeholder="选择具有导入权的数据来源"
                />
              </div>
            )}
            <Collapse
              className="upload-metadata"
              size="small"
              items={[
                {
                  key: 'optional',
                  label: '补充信息（可选）',
                  children: (
                    <div className="live-upload-fields">
                      <label htmlFor="live-course">课程（可选）</label>
                      <Input
                        id="live-course"
                        value={course}
                        maxLength={100}
                        onChange={(e) => setCourse(e.target.value)}
                        disabled={busy}
                      />
                      <label htmlFor="live-date">测评日期（可选）</label>
                      <Input
                        id="live-date"
                        type="date"
                        value={date}
                        onChange={(e) => setDate(e.target.value)}
                        disabled={busy}
                      />
                    </div>
                  ),
                },
              ]}
            />
            {!scopes.some((s) => s.can_import) && (
              <Alert type="warning" showIcon title="当前教师没有导入权，可查看已授权批次。" />
            )}
            <Upload.Dragger
              accept=".xlsx"
              multiple={false}
              showUploadList={false}
              beforeUpload={selectFile}
              disabled={busy || !allowed}
              className="excel-dropzone"
            >
              <div className="upload-symbol">
                <CloudUploadOutlined />
              </div>
              <h3>
                {busy
                  ? '正在检查文件…'
                  : selectedFile
                    ? '文件已选择，可重新选择替换'
                    : '拖拽文件至此，或选择文件'}
              </h3>
              <Button type="primary" tabIndex={-1} loading={busy} disabled={!allowed}>
                选择 Excel
              </Button>
              <p>Excel .xlsx · 最大 10 MiB · 文件能否解析以检查结果为准</p>
              <p>选择文件后可修改测评名称，点击下一步再预览。</p>
            </Upload.Dragger>
            {selectedFile && (
              <UploadSelection
                filename={selectedFile.name}
                name={title}
                onNameChange={setTitle}
                onNext={next}
                onClear={() => {
                  setUploadDraft(null);
                  setError(null);
                  setLocalError(null);
                }}
                busy={busy}
                allowDefaultName
                disabled={!allowed}
              />
            )}
            {localError && (
              <Alert className="session-notice" type="error" showIcon title={localError} />
            )}
            {error && <RequestError error={error} />}
          </Panel>
        </div>
      )}
      {view === 'history' && (
        <Panel
          title="历史测评"
          subtitle="查看已确认导入、且有权访问的测评。"
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
                { title: '状态', render: () => <Tag color="success">已导入</Tag> },
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
      <p className="muted small">测评仅向获授权教师展示；未审核原文不会直接展示。</p>
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
          <p className="muted small">评分标准版本：{batch.rubric_version}</p>
          <p className="muted small">该概况为已确认批次的评分统计。</p>
        </Panel>
      )}
    </Drawer>
  );
}
