'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Card, Flex, Select, Spin, Tag, Typography, Upload } from 'antd';
import { FileExcelTwoTone, FileZipTwoTone, UploadOutlined } from '@ant-design/icons';
import { tokens } from '../../app/theme';
import { useDataMode } from '../../app/data-mode';
import { answerAnalysisPath } from '../../app/workspace-routes';
import { assessmentNameFromFilename } from '../../services/assessment-name';
import { ApiError } from '../../services/edu-api/client';
import { isQuestionPreview } from '../../services/edu-api/question-types';
import LiveImportPreview from './LiveImportPreview';
import QuestionImportPreview from './QuestionImportPreview';
import { RequestError } from './LiveStatus';
import UploadWorkflow from './UploadWorkflow';
import { classifyUploadFiles } from './upload-classification';

function formatByteSize(bytes: number) {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KiB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
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
  const { scope, preview, file: selectedExcel } = draft;
  const setScope = (nextScope: string) => setUploadDraft({ ...draft, scope: nextScope });
  const allowed = scopes.some((s) => s.data_scope_id === scope && s.can_import);

  const [zipFiles, setZipFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
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

  async function checkExcel(file: File, nextDraft: typeof draft) {
    if (controllerRef.current || !allowed) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    setLocalError(null);
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
      if (!controller.signal.aborted && cause instanceof ApiError) setError(cause);
    } finally {
      if (!controller.signal.aborted) {
        controllerRef.current = null;
        setBusy(false);
      }
    }
  }

  async function submitAnswerSheets() {
    if (controllerRef.current || !zipFiles.length || !allowed) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setError(null);
    setLocalError(null);
    const form = new FormData();
    for (const file of zipFiles) form.append('file', file);
    form.append('data_scope_id', scope);
    try {
      const result = await client.uploadAnswerSheets(form, controller.signal);
      if (!controller.signal.aborted) {
        setZipFiles([]);
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
      }
    }
  }

  function resetSelection() {
    if (busy || controllerRef.current) return;
    setZipFiles([]);
    setError(null);
    setLocalError(null);
    if (selectedExcel || preview) setUploadDraft(null);
  }

  function handleSelection(selection: File[]) {
    if (busy || controllerRef.current) return;
    setError(null);
    setLocalError(null);
    if (!allowed) {
      setLocalError('请选择当前有导入权的数据来源。');
      return;
    }
    const classified = classifyUploadFiles(selection);
    if (classified.kind === 'invalid') {
      setLocalError(classified.message);
      return;
    }
    if (classified.kind === 'answer_sheets') {
      setUploadDraft(null);
      setZipFiles(classified.files);
      return;
    }
    setZipFiles([]);
    const nextDraft = {
      ...draft,
      file: classified.file,
      title: assessmentNameFromFilename(classified.file.name),
      preview: null,
      committed: null,
    };
    setUploadDraft(nextDraft);
    void checkExcel(classified.file, nextDraft);
  }

  if (preview) {
    return isQuestionPreview(preview) ? (
      <QuestionImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />
    ) : (
      <LiveImportPreview initial={preview} onCancel={() => setUploadDraft(null)} />
    );
  }

  const isAnswerSheetMode = zipFiles.length > 0;
  const hasSelection = isAnswerSheetMode || Boolean(selectedExcel);
  const firstUpload = !batch && !notice && !hasSelection;
  const zipTotalBytes = zipFiles.reduce((sum, file) => sum + file.size, 0);

  return (
    <div
      className={`page-stack data-page live-data${firstUpload ? ' first-assessment-upload' : ''}`}
    >
      <UploadWorkflow
        step={busy ? 1 : 0}
        busy={busy}
        steps={
          isAnswerSheetMode ? ['选择文件', '解析并查看'] : ['选择文件', '检查结果', '确认导入']
        }
        actions={
          isAnswerSheetMode ? (
            <>
              <Button disabled={busy} onClick={resetSelection}>
                重新选择
              </Button>
              <div className="workflow-next-action">
                <Button
                  type="primary"
                  loading={busy}
                  disabled={!allowed}
                  onClick={() => void submitAnswerSheets()}
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
          <Alert type="warning" showIcon title="当前账户没有上传权限，可查看已授权测评。" />
        )}
        <div className="workflow-upload-target" hidden={hasSelection || busy}>
          <Upload.Dragger
            accept=".xlsx,.zip"
            multiple
            showUploadList={false}
            disabled={busy || !allowed}
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
              <Button type="primary" tabIndex={-1} disabled={!allowed}>
                选择文件
              </Button>
              <p>
                支持拖入 .xlsx 测评表（单文件）或 .zip 编程答题卡（最多 20 个），总大小不超过 10
                MB，系统自动识别类型。
              </p>
            </div>
          </Upload.Dragger>
        </div>

        {isAnswerSheetMode && (
          <>
            <Card size="small" type="inner" className="selected-assessment-file">
              <Flex align="center" gap={tokens.sizeSM}>
                <FileZipTwoTone
                  twoToneColor={tokens.colorPrimary}
                  style={{ fontSize: 28, flex: 'none' }}
                  aria-hidden="true"
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Flex align="center" gap={tokens.sizeXS} wrap>
                    <Typography.Title level={5} style={{ margin: 0, overflowWrap: 'anywhere' }}>
                      {zipFiles.length === 1 ? zipFiles[0]!.name : `已选 ${zipFiles.length} 个文件`}
                    </Typography.Title>
                    <Tag color="blue">已识别：编程答题卡</Tag>
                  </Flex>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    {zipFiles.length === 1
                      ? formatByteSize(zipTotalBytes)
                      : `${zipFiles.length} 个 ZIP · 共 ${formatByteSize(zipTotalBytes)}`}
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
                  正在导入答题卡
                </Typography.Title>
              </div>
            )}
          </>
        )}

        {selectedExcel && !isAnswerSheetMode && (
          <>
            <Card size="small" type="inner" className="selected-assessment-file">
              <Flex align="center" gap={tokens.sizeSM}>
                <FileExcelTwoTone
                  twoToneColor={tokens.colorSuccess}
                  style={{ fontSize: 28, flex: 'none' }}
                  aria-hidden="true"
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Flex align="center" gap={tokens.sizeXS} wrap>
                    <Typography.Title level={5} style={{ margin: 0, overflowWrap: 'anywhere' }}>
                      {selectedExcel.name}
                    </Typography.Title>
                    <Tag color="green">已识别：测评数据</Tag>
                  </Flex>
                  <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                    {formatByteSize(selectedExcel.size)}
                  </Typography.Text>
                </div>
                <Button disabled={busy || !allowed} onClick={resetSelection}>
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
                <Button onClick={() => void checkExcel(selectedExcel, draft)} disabled={!allowed}>
                  重试检查
                </Button>
              </div>
            )}
          </>
        )}

        {localError && <Alert type="error" showIcon title={localError} />}
        {error && <RequestError error={error} />}
      </UploadWorkflow>
    </div>
  );
}
