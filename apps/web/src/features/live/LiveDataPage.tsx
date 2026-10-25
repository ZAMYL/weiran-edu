'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Drawer, Input, Select, Steps, Table, Tag, Upload } from 'antd';
import { CloudUploadOutlined, ReloadOutlined } from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import type { ApiBatch, ApiImport } from '../../services/edu-api/types';
import { dimensionName } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import LiveImportPreview from './LiveImportPreview';

export default function LiveDataPage() {
  const { client, scopes, batch, selectBatch, revision } = useDataMode();
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [course, setCourse] = useState('');
  const [date, setDate] = useState('');
  const [scope, setScope] = useState(scopes.find((s) => s.can_import)?.data_scope_id ?? '');
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<ApiImport | null>(null);
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
  async function upload(file: File) {
    if (!title.trim() || !allowed) {
      setLocalError('请填写测评名称，并选择当前有导入权的数据来源。');
      return Upload.LIST_IGNORE;
    }
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 10 * 1024 * 1024) {
      setLocalError('请选择不超过 10 MiB 的原始双 Sheet .xlsx 文件。');
      return Upload.LIST_IGNORE;
    }
    uploadController.current?.abort();
    const controller = new AbortController();
    uploadController.current = controller;
    setBusy(true);
    setError(null);
    setLocalError(null);
    const form = new FormData();
    form.append('file', file);
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
    return Upload.LIST_IGNORE;
  }
  const enter = (target: ApiBatch) => {
    selectBatch(target);
    router.push('/insights');
  };
  if (preview) return <LiveImportPreview initial={preview} onCancel={() => setPreview(null)} />;
  return (
    <div className="page-stack data-page live-data">
      <div className="data-top-grid">
        <Panel title="上传正式测评" className="upload-panel">
          <Steps
            current={0}
            size="small"
            items={[{ title: '上传文件' }, { title: '预览校验' }, { title: '确认入库' }]}
          />
          <div className="live-upload-fields">
            <label htmlFor="live-scope">授权数据来源</label>
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
            <label htmlFor="live-upload-title">测评名称</label>
            <Input
              id="live-upload-title"
              value={title}
              maxLength={100}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="填写本次测评名称"
              disabled={busy}
            />
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
          {!scopes.some((s) => s.can_import) && (
            <Alert type="warning" showIcon title="当前教师没有导入权，可查看已授权批次。" />
          )}
          <Upload.Dragger
            accept=".xlsx"
            multiple={false}
            showUploadList={false}
            beforeUpload={upload}
            disabled={busy || !allowed || !title.trim()}
            className="excel-dropzone"
          >
            <div className="upload-symbol">
              <CloudUploadOutlined />
            </div>
            <h3>{busy ? '服务端正在解析和校验…' : '选择原始双 Sheet 五维 Excel'}</h3>
            <Button
              type="primary"
              tabIndex={-1}
              loading={busy}
              disabled={!allowed || !title.trim()}
            >
              选择正式文件
            </Button>
            <p>固定五维双工作表模板 · .xlsx · 最大 10 MiB</p>
          </Upload.Dragger>
          {localError && (
            <Alert className="session-notice" type="error" showIcon title={localError} />
          )}
          {error && <RequestError error={error} />}
        </Panel>
        <Panel title="数据准备" className="preparation-panel">
          <div className="preparation-list">
            {[
              [
                '原始双工作表格式',
                'Sheet1 为评分依据，Sheet2 为学生原始五维评分。请使用原始双工作表文件。',
              ],
              ['服务端预览与质量报告', '评分范围 1–5、精度 0.01；错误阻断，警告需教师显式确认。'],
              ['确认入库后再分析', '预览不是成功入库；重复文件、预览版本和访问权限由后端核验。'],
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
        title="已入库的授权批次"
        subtitle="服务端分页读取，确认入库的批次保存在后端数据库，可在批次列表中再次查看。"
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
              { title: '学生人数', dataIndex: 'student_count' },
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
      <p className="drawer-subtitle">{batch.title}</p>
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
