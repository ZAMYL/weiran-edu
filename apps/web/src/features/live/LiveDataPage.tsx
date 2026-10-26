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

export default function LiveDataPage() {
  const { client, scopes, batch, selectBatch, revision } = useDataMode();
  const router = useRouter();
  const [course, setCourse] = useState('');
  const [date, setDate] = useState('');
  const [scope, setScope] = useState(scopes.find((s) => s.can_import)?.data_scope_id ?? '');
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<ApiImport | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
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
    setSelectedFile(null);
    if (!allowed) {
      setLocalError('请选择当前有导入权的数据来源。');
      return Upload.LIST_IGNORE;
    }
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 10 * 1024 * 1024) {
      setLocalError('请选择不超过 10 MiB 的原始双 Sheet .xlsx 文件。');
      return Upload.LIST_IGNORE;
    }
    setSelectedFile(file);
    setTitle(assessmentNameFromFilename(file.name));
    return Upload.LIST_IGNORE;
  }
  async function next() {
    if (busy || !selectedFile || !title.trim() || !allowed) return;
    uploadController.current?.abort();
    const controller = new AbortController();
    uploadController.current = controller;
    setBusy(true);
    setError(null);
    setLocalError(null);
    const form = new FormData();
    form.append('file', selectedFile);
    form.append('data_scope_id', scope);
    form.append('title', title.trim());
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
  if (preview) return <LiveImportPreview initial={preview} onCancel={() => setPreview(null)} />;
  return (
    <div className="page-stack data-page live-data">
      <div className="data-top-grid">
        <Panel title="上传测评" className="upload-panel">
          <Steps
            current={0}
            size="small"
            items={[{ title: '选择文件' }, { title: '自动检查' }, { title: '确认导入' }]}
          />
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
            <h3>{busy ? '正在检查文件…' : '选择 Excel 测评文件'}</h3>
            <Button type="primary" tabIndex={-1} loading={busy} disabled={!allowed}>
              选择 Excel
            </Button>
            <p>.xlsx · 双工作表五维模板 · 最大 10 MiB</p>
            <p>选择文件后可修改测评名称，点击下一步再预览。</p>
          </Upload.Dragger>
          {selectedFile && (
            <UploadSelection
              filename={selectedFile.name}
              name={title}
              onNameChange={setTitle}
              onNext={next}
              onClear={() => {
                setSelectedFile(null);
                setTitle('');
                setError(null);
                setLocalError(null);
              }}
              busy={busy}
              disabled={!allowed}
            />
          )}
          {localError && (
            <Alert className="session-notice" type="error" showIcon title={localError} />
          )}
          {error && <RequestError error={error} />}
        </Panel>
        <Panel title="数据准备" className="preparation-panel">
          <div className="preparation-list">
            {[
              ['原始双工作表格式', 'Sheet1 为评分依据，Sheet2 为参评者五维评分。'],
              ['自动检查', '评分范围 1–5，最多两位小数；错误修正后再导入。'],
              ['确认导入', '查看检查结果，有提醒时确认后再导入。'],
            ].map(([heading, description]) => (
              <div className="preparation-item" key={heading}>
                <div>
                  <h3>{heading}</h3>
                  <p>{description}</p>
                </div>
              </div>
            ))}
          </div>
          <Alert
            type="info"
            showIcon
            title="正式数据与 Demo 独立"
            description="本模式只展示服务端返回的授权批次和评分；不会使用本地样例补齐分析。非空原始评语仍可能待审核。"
          />
        </Panel>
      </div>
      <Panel
        title="已导入测评"
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
            scroll={{ x: 1100 }}
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
              {
                title: '来源摘要',
                dataIndex: 'file_sha256',
                render: (value) => <code>{String(value).slice(0, 12)}…</code>,
              },
              { title: '参评人数', dataIndex: 'student_count' },
              { title: '评分记录', dataIndex: 'evaluation_count' },
              { title: '状态', render: () => <Tag color="success">服务端已入库</Tag> },
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
      <Panel title="数据使用说明" className="data-usage">
        <p className="muted">
          教师权限由后端逐请求核验；批次持久化在服务端，凭证只保留在当前内存会话。没有授权的原文不会显示，不生成演示教学结论。
        </p>
      </Panel>
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
    <Drawer title="正式批次评分概况" open onClose={onClose} size={550}>
      <AssessmentContext title={batch.title} course={batch.course} date={batch.assessment_date} />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && (
        <Panel title="服务端完整性统计">
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
