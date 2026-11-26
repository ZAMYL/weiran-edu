'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Flex,
  Input,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  FileExcelTwoTone,
  FileTextOutlined,
  FileUnknownOutlined,
  FileZipTwoTone,
  UploadOutlined,
} from '@ant-design/icons';
import { tokens } from '../../app/theme';
import { useDataMode } from '../../app/data-mode';
import { answerAnalysisPath } from '../../app/workspace-routes';
import { assessmentNameFromFilename } from '../../services/assessment-name';
import { ApiError } from '../../services/edu-api/client';
import {
  materialAnalysisLabel,
  materialFieldMeaningLabel,
  materialKindLabel,
  materialRoleLabel,
  materialRoleStateLabel,
  type MaterialRecognition,
} from '../../services/edu-api/material-inspect-types';
import { isQuestionPreview } from '../../services/edu-api/question-types';
import LiveImportPreview from './LiveImportPreview';
import QuestionImportPreview from './QuestionImportPreview';
import { RequestError } from './LiveStatus';
import UploadWorkflow from './UploadWorkflow';

const MAX_UPLOAD_FILES = 20;
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

function formatByteSize(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KiB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

function roleStateColor(state: MaterialRecognition['role_state']) {
  if (state === 'candidate') return 'success';
  if (state === 'needs_confirmation') return 'warning';
  return 'error';
}

function renderKindIcon(rec: MaterialRecognition | undefined) {
  if (!rec) {
    return (
      <FileTextOutlined
        style={{ fontSize: 28, color: tokens.colorPrimary, flex: 'none' }}
        aria-hidden="true"
      />
    );
  }
  if (!rec.supported || rec.route === 'unsupported') {
    return (
      <FileUnknownOutlined
        style={{ fontSize: 28, color: tokens.colorError, flex: 'none' }}
        aria-hidden="true"
      />
    );
  }
  if (rec.route === 'assessment_import') {
    return (
      <FileZipTwoTone
        twoToneColor={tokens.colorPrimary}
        style={{ fontSize: 28, flex: 'none' }}
        aria-hidden="true"
      />
    );
  }
  if (rec.kind === 'xlsx') {
    return (
      <FileExcelTwoTone
        twoToneColor={tokens.colorSuccess}
        style={{ fontSize: 28, flex: 'none' }}
        aria-hidden="true"
      />
    );
  }
  return (
    <FileTextOutlined
      style={{ fontSize: 28, color: tokens.colorPrimary, flex: 'none' }}
      aria-hidden="true"
    />
  );
}

export default function UnifiedUploadPage() {
  const router = useRouter();
  const { client, scopes, batch, notice, uploadDraft, setUploadDraft } = useDataMode();
  const importScopes = scopes.filter((s) => s.can_import);
  const draft = uploadDraft ?? {
    course: '',
    date: '',
    scope:
      importScopes.find((s) => s.data_scope_id === batch?.data_scope_id)?.data_scope_id ??
      (importScopes.length === 1 ? importScopes[0]!.data_scope_id : ''),
    preview: null,
    file: null,
    title: '',
    committed: null,
  };
  const { scope, preview } = draft;
  const setScope = (nextScope: string) => setUploadDraft({ ...draft, scope: nextScope });
  const allowed = scopes.some((s) => s.data_scope_id === scope && s.can_import);

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [inspections, setInspections] = useState<MaterialRecognition[]>([]);
  const [customTitle, setCustomTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [busyLabel, setBusyLabel] = useState('');
  const [error, setError] = useState<ApiError | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(
    () => () => {
      if (controllerRef.current) {
        controllerRef.current.abort();
        controllerRef.current = null;
        setBusy(false);
      }
    },
    [],
  );

  function resetSelection() {
    if (busy || controllerRef.current) return;
    setSelectedFiles([]);
    setInspections([]);
    setCustomTitle('');
    setError(null);
    setLocalError(null);
    if (uploadDraft) setUploadDraft(null);
  }

  async function inspectFiles(files: File[]) {
    if (controllerRef.current) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setBusyLabel('正在识别资料内容');
    setError(null);
    setLocalError(null);
    try {
      const results = await Promise.all(
        files.map((file) => {
          const form = new FormData();
          form.append('file', file);
          return client.inspectMaterial(form, controller.signal);
        }),
      );
      if (!controller.signal.aborted) {
        setInspections(results);
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof ApiError ? cause : new ApiError(502, 'INVALID_API_RESPONSE'));
      }
    } finally {
      if (!controller.signal.aborted) {
        controllerRef.current = null;
        setBusy(false);
        setBusyLabel('');
      }
    }
  }

  async function checkExcelPreview(file: File) {
    if (controllerRef.current || !allowed) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setBusyLabel('正在检查测评表数据');
    setError(null);
    setLocalError(null);
    const resolvedTitle = customTitle.trim() || assessmentNameFromFilename(file.name);
    const nextDraft = {
      ...draft,
      file,
      title: resolvedTitle,
      preview: null,
      committed: null,
    };
    setUploadDraft(nextDraft);
    const form = new FormData();
    form.append('file', file);
    form.append('data_scope_id', nextDraft.scope);
    if (nextDraft.title.trim()) form.append('title', nextDraft.title.trim());
    if (nextDraft.course.trim()) form.append('course', nextDraft.course.trim());
    if (nextDraft.date) form.append('assessment_date', nextDraft.date);
    try {
      const result = await client.preview(form, controller.signal);
      if (!controller.signal.aborted) {
        setUploadDraft({ ...nextDraft, preview: result, committed: null });
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof ApiError ? cause : new ApiError(502, 'INVALID_API_RESPONSE'));
      }
    } finally {
      if (!controller.signal.aborted) {
        controllerRef.current = null;
        setBusy(false);
        setBusyLabel('');
      }
    }
  }

  async function submitAnswerSheets() {
    if (controllerRef.current || !selectedFiles.length || !allowed) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setBusyLabel('正在导入答题卡');
    setError(null);
    setLocalError(null);
    const form = new FormData();
    for (const file of selectedFiles) form.append('file', file);
    form.append('data_scope_id', scope);
    if (customTitle.trim()) form.append('title', customTitle.trim());
    try {
      const result = await client.uploadAnswerSheets(form, controller.signal);
      if (!controller.signal.aborted) {
        setSelectedFiles([]);
        setInspections([]);
        router.push(answerAnalysisPath(result.assessment_id));
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof ApiError ? cause : new ApiError(502, 'INVALID_API_RESPONSE'));
      }
    } finally {
      if (!controller.signal.aborted) {
        controllerRef.current = null;
        setBusy(false);
        setBusyLabel('');
      }
    }
  }

  function handleSelection(selection: File[]) {
    if (busy || controllerRef.current) return;
    setError(null);
    setLocalError(null);
    if (!selection.length) {
      setLocalError('请选择要上传的文件。');
      return;
    }
    if (selection.length > MAX_UPLOAD_FILES) {
      setLocalError(`单次最多选择 ${MAX_UPLOAD_FILES} 个文件。`);
      return;
    }
    const totalBytes = selection.reduce((sum, file) => sum + file.size, 0);
    if (totalBytes > MAX_UPLOAD_BYTES) {
      setLocalError('文件总大小超过 10 MB，请缩小后重新选择。');
      return;
    }
    setUploadDraft(null);
    setSelectedFiles(selection);
    setCustomTitle(assessmentNameFromFilename(selection[0]!.name));
    setInspections([]);
    void inspectFiles(selection);
  }

  if (preview) {
    return isQuestionPreview(preview) ? (
      <QuestionImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />
    ) : (
      <LiveImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />
    );
  }

  const hasSelection = selectedFiles.length > 0;
  const totalBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  const firstUpload = !batch && !notice && !hasSelection;
  const primaryInspection = inspections[0];
  const allAnswerSheets =
    inspections.length > 0 &&
    inspections.every((item) => item.supported && item.route === 'assessment_import');
  const isSingle = selectedFiles.length === 1;
  const canPreviewXlsx =
    isSingle &&
    primaryInspection?.supported &&
    primaryInspection.route === 'material_understanding' &&
    primaryInspection.kind === 'xlsx';

  let actions = undefined;
  if (hasSelection && !busy) {
    if (allAnswerSheets) {
      actions = (
        <>
          <Button onClick={resetSelection}>重新选择</Button>
          <div className="workflow-next-action">
            <Button type="primary" disabled={!allowed} onClick={() => void submitAnswerSheets()}>
              导入答题卡并查看分析
            </Button>
          </div>
        </>
      );
    } else if (canPreviewXlsx && selectedFiles[0]) {
      const targetFile = selectedFiles[0];
      actions = (
        <>
          <Button onClick={resetSelection}>重新选择</Button>
          <div className="workflow-next-action">
            <Button
              type="primary"
              disabled={!allowed}
              onClick={() => void checkExcelPreview(targetFile)}
            >
              继续检查并导入测评表
            </Button>
          </div>
        </>
      );
    } else if (!inspections.length && error) {
      actions = (
        <>
          <Button onClick={resetSelection}>重新选择</Button>
          <div className="workflow-next-action">
            <Button type="primary" onClick={() => void inspectFiles(selectedFiles)}>
              重试服务端识别
            </Button>
          </div>
        </>
      );
    }
  }

  return (
    <div
      className={`page-stack data-page live-data${firstUpload ? ' first-assessment-upload' : ''}`}
    >
      <UploadWorkflow
        step={busy || inspections.length > 0 ? 1 : 0}
        busy={busy}
        steps={['选择文件', '服务端识别', '确认与导入']}
        actions={actions}
      >
        {importScopes.length > 1 && (
          <div className="live-upload-fields">
            <label htmlFor="unified-upload-scope">数据来源</label>
            <Select
              id="unified-upload-scope"
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
          <Alert
            type="warning"
            showIcon
            title="当前账户没有导入权，可查看已授权测评或仅做资料识别。"
          />
        )}

        <div className="workflow-upload-target" hidden={hasSelection || busy}>
          <Upload.Dragger
            multiple
            showUploadList={false}
            disabled={busy}
            className="excel-dropzone workflow-dropzone"
            beforeUpload={(file, selection) => {
              if (file.uid === selection[0]?.uid) {
                handleSelection(selection);
              }
              return Upload.LIST_IGNORE;
            }}
          >
            <div className="assessment-file-prompt">
              <UploadOutlined className="assessment-file-symbol" aria-hidden="true" />
              <Button type="primary" tabIndex={-1} disabled={busy}>
                选择文件
              </Button>
              <p>直接拖入资料文件（如 XLSX、CSV、HTML 或编程答题卡压缩包，单批最大 10 MB）</p>
            </div>
          </Upload.Dragger>
        </div>

        {hasSelection && (
          <Flex vertical gap={tokens.sizeSM}>
            <Card size="small" type="inner" className="selected-assessment-file">
              <Flex align="center" gap={tokens.sizeSM}>
                {renderKindIcon(isSingle ? primaryInspection : undefined)}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Flex align="center" gap={tokens.sizeXS} wrap>
                    <Typography.Title level={5} style={{ margin: 0, overflowWrap: 'anywhere' }}>
                      {isSingle ? selectedFiles[0]!.name : `已选 ${selectedFiles.length} 个文件`}
                    </Typography.Title>
                    {isSingle && primaryInspection && (
                      <>
                        <Tag color={roleStateColor(primaryInspection.role_state)}>
                          {materialKindLabel(primaryInspection.kind)}
                        </Tag>
                        <Tag color={roleStateColor(primaryInspection.role_state)}>
                          {materialRoleLabel(primaryInspection.role, primaryInspection.role_state)}
                          {' · '}
                          {materialRoleStateLabel(primaryInspection.role_state)}
                        </Tag>
                      </>
                    )}
                    {!isSingle && allAnswerSheets && <Tag color="blue">内容已识别：编程答题卡</Tag>}
                  </Flex>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    {isSingle
                      ? formatByteSize(totalBytes)
                      : `${selectedFiles.length} 个文件 · 共 ${formatByteSize(totalBytes)}`}
                  </Typography.Text>
                </div>
                <Button disabled={busy} onClick={resetSelection}>
                  更换文件
                </Button>
              </Flex>
            </Card>

            {busy && (
              <div className="workflow-checking" role="status">
                <Spin />
                <Typography.Title level={4} style={{ margin: 0 }}>
                  {busyLabel || '正在处理文件'}
                </Typography.Title>
              </div>
            )}

            {!busy && (allAnswerSheets || canPreviewXlsx) && (
              <div className="live-upload-fields">
                <label htmlFor="unified-upload-title">测评名称</label>
                <Input
                  id="unified-upload-title"
                  aria-label="测评名称"
                  value={customTitle}
                  maxLength={100}
                  placeholder="输入测评名称（留空则使用默认文件名）"
                  onChange={(event) => setCustomTitle(event.target.value)}
                />
              </div>
            )}

            {!busy && isSingle && primaryInspection && (
              <SingleInspectionResult recognition={primaryInspection} />
            )}

            {!busy && !isSingle && inspections.length > 0 && (
              <Card size="small" type="inner" title="多文件内容识别结果">
                <Flex vertical gap={tokens.sizeSM}>
                  {!allAnswerSheets && (
                    <Alert
                      type="warning"
                      showIcon
                      title="多文件批传当前仅支持全部识别为编程答题卡的压缩包"
                      description="列表中存在非答题卡资料或无法确认的结构化文件，请改为单文件上传识别。"
                    />
                  )}
                  <Table
                    rowKey="rowKey"
                    size="small"
                    pagination={false}
                    scroll={{ x: 'max-content' }}
                    dataSource={inspections.map((item, idx) => ({
                      ...item,
                      rowKey: `${item.source_id}:${idx}`,
                      displayName: selectedFiles[idx]?.name ?? item.file_name ?? `文件 ${idx + 1}`,
                    }))}
                    columns={[
                      { title: '文件名', dataIndex: 'displayName' },
                      {
                        title: '识别格式',
                        render: (_, row) => materialKindLabel(row.kind),
                      },
                      {
                        title: '识别角色',
                        render: (_, row) => (
                          <Tag color={roleStateColor(row.role_state)}>
                            {materialRoleLabel(row.role, row.role_state)}
                          </Tag>
                        ),
                      },
                      {
                        title: '待确认说明',
                        render: (_, row) =>
                          row.pending.length ? row.pending.map((p) => p.detail).join('；') : '无',
                      },
                    ]}
                  />
                </Flex>
              </Card>
            )}
          </Flex>
        )}

        {localError && <Alert type="error" showIcon title={localError} />}
        {error && <RequestError error={error} />}
      </UploadWorkflow>
    </div>
  );
}

function SingleInspectionResult({ recognition }: { recognition: MaterialRecognition }) {
  if (!recognition.supported || recognition.route === 'unsupported') {
    return (
      <Alert
        type="error"
        showIcon
        title={`服务端不支持处理此文件（${materialKindLabel(recognition.kind)}）`}
        description={
          recognition.pending.length
            ? recognition.pending.map((item) => item.detail).join('；')
            : '当前仅支持 XLSX、CSV、HTML 表格与编程答题卡 ZIP。'
        }
      />
    );
  }

  return (
    <Flex vertical gap={tokens.sizeSM}>
      {recognition.pending.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title={`识别完成，但有 ${recognition.pending.length} 项需要确认`}
          description={
            <Flex vertical gap={4}>
              {recognition.pending.map((item, idx) => (
                <Typography.Text key={`${item.code}:${idx}`} style={{ fontSize: 13 }}>
                  • {item.detail}
                </Typography.Text>
              ))}
            </Flex>
          }
        />
      )}

      <Card size="small" type="inner" title="服务端识别摘要">
        <Flex vertical gap={tokens.sizeSM}>
          <Descriptions
            size="small"
            bordered
            column={{ xs: 1, sm: 2 }}
            items={[
              {
                key: 'kind',
                label: '文件格式',
                children: materialKindLabel(recognition.kind),
              },
              {
                key: 'role',
                label: '资料角色',
                children: (
                  <Space size="small" wrap>
                    <span>{materialRoleLabel(recognition.role, recognition.role_state)}</span>
                    <Tag color={roleStateColor(recognition.role_state)}>
                      {materialRoleStateLabel(recognition.role_state)}
                    </Tag>
                  </Space>
                ),
              },
              {
                key: 'route',
                label: '建议处理路径',
                children:
                  recognition.route === 'assessment_import' ? '编程答题卡导入' : '通用资料结构理解',
              },
              {
                key: 'source',
                label: '内容摘要 (SHA-256)',
                children: (
                  <Typography.Text code style={{ fontSize: 12 }}>
                    {recognition.source_id.replace(/^sha256:/, '').slice(0, 12)}…
                  </Typography.Text>
                ),
              },
            ]}
          />

          {recognition.tables.length > 0 && (
            <Flex align="center" wrap gap={tokens.sizeXS}>
              <Typography.Text type="secondary">识别数据表：</Typography.Text>
              {recognition.tables.map((table) => (
                <Tag
                  key={table.ref}
                  color={table.blocked ? 'error' : table.complete ? 'success' : 'processing'}
                >
                  {table.ref}（{table.rows} 行 · {table.columns} 列 ·{' '}
                  {table.blocked ? '已阻塞' : table.complete ? '完整覆盖' : '部分行观测'}）
                </Tag>
              ))}
            </Flex>
          )}

          {recognition.analyses.length > 0 && (
            <Flex align="center" wrap gap={tokens.sizeXS}>
              <Typography.Text type="secondary">可用分析能力：</Typography.Text>
              {recognition.analyses.map((code) => (
                <Tag key={code} color="blue">
                  {materialAnalysisLabel(code)}
                </Tag>
              ))}
            </Flex>
          )}

          {recognition.fields.length > 0 && (
            <Table
              rowKey="column"
              size="small"
              pagination={false}
              scroll={{ x: 'max-content' }}
              dataSource={recognition.fields}
              columns={[
                { title: '列 / 位置', dataIndex: 'column' },
                {
                  title: '识别字段含义',
                  render: (_, field) => <Tag>{materialFieldMeaningLabel(field.meaning)}</Tag>,
                },
                {
                  title: '识别依据',
                  render: (_, field) => field.evidence.join('、') || '—',
                },
              ]}
            />
          )}
        </Flex>
      </Card>

      {recognition.route === 'material_understanding' && recognition.kind !== 'xlsx' && (
        <Alert
          type="info"
          showIcon
          title="已完成服务端结构识别"
          description="后端已完成该文件的格式探测、角色判断与候选字段识别（/api/v1/edu/materials）；通用资料的教师逐项确认与业务事实落库界面待后续前端接入。"
        />
      )}
    </Flex>
  );
}
