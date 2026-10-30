'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';
import {
  Alert,
  App,
  Button,
  Drawer,
  Input,
  Progress,
  Select,
  Steps,
  Table,
  Tag,
  Upload,
} from 'antd';
import {
  CheckCircleFilled,
  CloudUploadOutlined,
  DownloadOutlined,
  FileExcelOutlined,
  InfoCircleOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { useBatch, useWorkspace } from '../../app/store';
import { DEMO_STANDARD } from '../../demo/demo';
import type { Batch, ImportPreview as Preview } from '../../domain/model';
import { parseExcel } from '../../services/excel';
import { Panel } from '../../components/ui';
import { ImportPreview } from './ImportPreview';
import { UploadSelection } from './UploadSelection';
import { assessmentNameFromFilename } from '../../services/assessment-name';

export default function DataPage() {
  const batch = useBatch();
  const { batches, setBatch, addBatch } = useWorkspace();
  const { fullscreen } = useWorkspaceLayout();
  const { message } = App.useApp();
  const router = useRouter();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [qualityBatch, setQualityBatch] = useState<Batch | null>(null);
  const uploadRequest = useRef(0);
  useEffect(
    () => () => {
      uploadRequest.current++;
    },
    [],
  );
  function selectFile(file: File) {
    if (loading) return Upload.LIST_IGNORE;
    setError(null);
    setSelectedFile(null);
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
      setError('请选择 .xlsx 格式的 Excel 文件。');
      return Upload.LIST_IGNORE;
    }
    setSelectedFile(file);
    setTitle(assessmentNameFromFilename(file.name));
    return Upload.LIST_IGNORE;
  }
  async function next() {
    if (loading || !selectedFile || !title.trim()) return;
    const request = ++uploadRequest.current;
    setError(null);
    setLoading(true);
    try {
      const parsed = await parseExcel(selectedFile, DEMO_STANDARD);
      if (request !== uploadRequest.current) return;
      if (useWorkspace.getState().batches.some((b) => b.fingerprint === parsed.fingerprint))
        throw new Error('这份文件已在当前会话中导入，重命名文件也不会创建重复批次。');
      setPreview(parsed);
    } catch (cause) {
      if (request === uploadRequest.current)
        setError(
          cause instanceof Error ? cause.message : '文件解析失败，请检查 Excel 文件是否完整。',
        );
    } finally {
      if (request === uploadRequest.current) setLoading(false);
    }
  }
  function confirm(name: string) {
    if (!preview || preview.issues.length || !name) return;
    if (useWorkspace.getState().batches.some((b) => b.fingerprint === preview.fingerprint)) {
      message.error('该文件已经导入。');
      return;
    }
    addBatch({
      id: crypto.randomUUID(),
      name,
      filename: preview.filename,
      fingerprint: preview.fingerprint,
      demo: false,
      standard: preview.standard,
      students: preview.students,
    });
    setPreview(null);
    setSelectedFile(null);
    setTitle('');
    message.success('已创建独立的本地批次；刷新页面后不保留。');
  }
  const enterInsights = (target: Batch) => {
    if (target.id !== batch.id) setBatch(target.id);
    router.push('/insights');
  };
  if (preview)
    return (
      <ImportPreview
        key={preview.fingerprint}
        preview={preview}
        initialName={title.trim()}
        onCancel={() => setPreview(null)}
        onConfirm={confirm}
      />
    );
  return (
    <div className={`data-page page-stack ${fullscreen ? 'data-fullscreen' : ''}`}>
      <div className="data-top-grid">
        <Panel title="上传新测评" className="upload-panel">
          <Steps
            current={0}
            size="small"
            items={[{ title: '选择文件' }, { title: '自动检查' }, { title: '确认导入' }]}
          />
          <div className="standard-select">
            <label htmlFor="standard-select">评价标准</label>
            <Select
              id="standard-select"
              aria-label="导入评价标准"
              value={DEMO_STANDARD.id}
              options={[{ value: DEMO_STANDARD.id, label: DEMO_STANDARD.name }]}
            />
          </div>
          <Upload.Dragger
            accept=".xlsx"
            multiple={false}
            disabled={loading}
            showUploadList={false}
            beforeUpload={selectFile}
            className="excel-dropzone"
          >
            <div className="upload-symbol">
              <CloudUploadOutlined />
            </div>
            <h3>{loading ? '正在解析和校验…' : '拖拽 Excel 到这里，或选择文件'}</h3>
            <Button type="primary" size="large" loading={loading} tabIndex={-1}>
              选择文件
            </Button>
            <p>长表格式 · .xlsx · 原始内容本地解析</p>
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
              }}
              busy={loading}
            />
          )}
          <div className="upload-note">
            <InfoCircleOutlined /> 保留原始评分、评语与来源；确认后才创建批次。
          </div>
          {error && (
            <Alert
              className="upload-error"
              type="error"
              showIcon
              title="文件未能导入"
              description={error}
              closable
              onClose={() => setError(null)}
            />
          )}
          <div className="template-links">
            <Button type="link" href="/templates/评价长表模板.xlsx" icon={<DownloadOutlined />}>
              下载长表模板
            </Button>
            <Button type="link" href="/templates/评价_待校验示例.xlsx">
              下载校验示例
            </Button>
          </div>
        </Panel>
        <Panel title="上传说明" className="preparation-panel">
          <details>
            <summary>文件要求与处理方式</summary>
            <div className="preparation-list">
              {[
                ['学生标识、五项评分与原始评语', '每名学生每个维度一条记录，表头请参照模板。'],
                ['评分范围与缺失检查', '按所选标准检查范围、步长、缺失与重复记录。'],
                ['重复文件检查', '通过文件内容指纹识别本会话内已导入文件。'],
              ].map(([title, detail]) => (
                <div className="preparation-item" key={title}>
                  <CheckCircleFilled />
                  <div>
                    <h3>{title}</h3>
                    <p>{detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </details>
          <Alert
            type="info"
            showIcon
            title="新测评将创建独立批次"
            description="当前仅提供演示评分标准；正式使用前需补充实际评价标准。"
          />
          <div className="local-storage-note">
            <InfoCircleOutlined /> 本地演示版：刷新后清除新增批次。
          </div>
        </Panel>
      </div>
      <Panel
        title="已导入测评"
        subtitle={`当前会话共 ${batches.length} 个批次；当前批次 ${batch.students.length} 名学生、${batch.students.length * batch.standard.dimensions.length} 条评分。`}
        action={
          <Input
            allowClear
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索测评名称或来源文件"
            aria-label="搜索已导入测评"
            className="batch-search"
          />
        }
        className="batches-panel"
      >
        <Table<Batch>
          rowKey="id"
          pagination={false}
          scroll={{ x: 1050 }}
          dataSource={batches.filter((b) =>
            `${b.name} ${b.filename}`.toLowerCase().includes(search.toLowerCase()),
          )}
          locale={{ emptyText: '没有找到匹配的测评' }}
          columns={[
            {
              title: '测评名称',
              key: 'name',
              width: 275,
              render: (_, b) => (
                <div className="batch-title">
                  <strong>{b.name}</strong>
                  {b.id === batch.id && <Tag color="blue">当前批次</Tag>}
                </div>
              ),
            },
            { title: '来源文件', dataIndex: 'filename' },
            { title: '参评人数', render: (_, b) => `${b.students.length} 人` },
            {
              title: '评分条数',
              render: (_, b) => `${b.students.length * b.standard.dimensions.length} 条`,
            },
            { title: '评价维度', render: (_, b) => `${b.standard.dimensions.length} 个` },
            {
              title: '数据状态',
              width: 110,
              render: (_, b) => (
                <Tag color={b.demo ? 'blue' : 'success'}>{b.demo ? '演示批次' : '本地已导入'}</Tag>
              ),
            },
            {
              title: '操作',
              width: 230,
              render: (_, b) => (
                <div className="batch-actions">
                  <Button onClick={() => setQualityBatch(b)}>查看质量报告</Button>
                  <Button type="primary" onClick={() => enterInsights(b)}>
                    查看学情
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </Panel>
      {fullscreen && (
        <Panel title="当前批次数据概览" className="completeness-panel">
          <Completeness batch={batch} />
        </Panel>
      )}
      <Panel title="数据使用说明" className="data-usage">
        <p className="muted">
          学生以匿名标识展示，原始评语及来源行号保留。演示批次使用合成记录；新增文件仅在浏览器中解析，本版不连接服务器或数据库。
        </p>
      </Panel>
      <Drawer
        title="当前批次质量报告"
        open={Boolean(qualityBatch)}
        onClose={() => setQualityBatch(null)}
        size={580}
      >
        {qualityBatch && (
          <>
            <p className="drawer-subtitle">{qualityBatch.name}</p>
            <Alert
              type="info"
              showIcon
              title={
                qualityBatch.demo
                  ? '合成演示记录，不代表真实文件校验结果'
                  : '文件已通过所选标准校验'
              }
            />
            <Panel title="评分完整性">
              <Completeness batch={qualityBatch} />
            </Panel>
            <Panel title="原始来源">
              <p>
                <FileExcelOutlined /> {qualityBatch.filename}
              </p>
              <p>
                工作表：
                {[
                  ...new Set(
                    qualityBatch.students.flatMap((s) =>
                      Object.values(s.ratings).map((r) => r.source.sheet),
                    ),
                  ),
                ].join('、')}
              </p>
              <p>评价标准：{qualityBatch.standard.name}</p>
              <p>标准版本：{qualityBatch.standard.version ?? '未指定'}</p>
              <p className="muted">原始评语、工作表、行号和评分列已保留。</p>
            </Panel>
          </>
        )}
      </Drawer>
    </div>
  );
}
function Completeness({ batch }: { batch: Batch }) {
  return (
    <div className="completeness-rows">
      {batch.standard.dimensions.map((d) => {
        const count = batch.students.filter((s) => s.ratings[d.id] != null).length;
        return (
          <div key={d.id}>
            <span>{d.name}</span>
            <span className="muted">
              {count} / {batch.students.length}
            </span>
            <Progress percent={batch.students.length ? (count / batch.students.length) * 100 : 0} />
          </div>
        );
      })}
    </div>
  );
}
