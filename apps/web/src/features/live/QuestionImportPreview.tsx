'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, Button, Checkbox, Input, Table } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { ApiError } from '../../services/edu-api/client';
import { itemScaleLabel } from '../../services/edu-api/assessment-items';
import { qualityLabels, type QuestionPreview } from '../../services/edu-api/question-types';
import UploadWorkflow from './UploadWorkflow';
import { RequestError, LoadingPanel } from './LiveStatus';
import { useRemote } from './useRemote';

export default function QuestionImportPreview({
  initial,
  onCancel,
}: {
  initial: QuestionPreview;
  onCancel: () => void;
}) {
  const { client, uploadDraft, setUploadDraft, batchChanged } = useDataMode();
  const router = useRouter();
  const [confirmation, setConfirmation] = useState(false);
  const [title, setTitle] = useState(initial.title);
  const [ack, setAck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const load = useCallback(
    (signal: AbortSignal) => client.questionImport(initial.import_id, signal),
    [client, initial.import_id],
  );
  const resource = useRemote('question-preview:' + initial.import_id, load);
  const view = resource.data;
  const edited = view && title.trim() !== view.title;
  const committed = uploadDraft?.committed;
  const canConfirm =
    !!view &&
    view.confirmable &&
    !edited &&
    !busy &&
    (!view.quality_issues.length || ack === view.preview_version);
  async function saveName() {
    if (!view || !title.trim() || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      const updated = await client.patchQuestionImport(
        view.import_id,
        view.preview_version,
        title.trim(),
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (uploadDraft) setUploadDraft({ ...uploadDraft, preview: updated });
      setTitle(updated.title);
      setAck(null);
      resource.retry();
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setError(e);
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
      }
    }
  }
  async function confirm() {
    if (!view || (!committed && !canConfirm) || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      const result =
        committed ??
        (await client.commit(
          view.import_id,
          view.preview_version,
          ack === view.preview_version,
          controller.signal,
        ));
      if (controller.signal.aborted) return;
      if (uploadDraft) setUploadDraft({ ...uploadDraft, committed: result });
      const batch = await client.batch(result.batch_id, controller.signal);
      if (controller.signal.aborted) return;
      if (batch.snapshot_id !== result.snapshot_id) throw new ApiError(409, 'SNAPSHOT_MISMATCH');
      batchChanged(batch);
      router.replace('/insights');
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setError(e);
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
      }
    }
  }
  return (
    <UploadWorkflow
      step={confirmation || committed ? 2 : 1}
      busy={busy || resource.loading}
      actions={
        confirmation || committed ? (
          <>
            <Button onClick={() => setConfirmation(false)} disabled={busy || !!committed}>
              返回检查
            </Button>
            <Button
              type="primary"
              disabled={busy || (!committed && !canConfirm)}
              loading={busy}
              onClick={confirm}
            >
              {committed ? '重新打开诊断' : '确认导入'}
            </Button>
          </>
        ) : (
          <>
            {!!view?.quality_issues.length && (
              <Checkbox
                checked={ack === view.preview_version}
                onChange={(e) => setAck(e.target.checked ? view.preview_version : null)}
              >
                我已查看评分提醒，确认继续导入
              </Checkbox>
            )}
            <Button type="primary" disabled={!canConfirm} onClick={() => setConfirmation(true)}>
              下一步
            </Button>
          </>
        )
      }
    >
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {error && <RequestError error={error} retry={resource.retry} />}
      {committed && <Alert type="success" showIcon title="文件已确认导入，可重试打开诊断。" />}
      {view && (
        <>
          {confirmation || committed ? (
            <h2>{view.title}</h2>
          ) : (
            <div className="batch-name-field">
              <label htmlFor="question-title">测评名称</label>
              <Input
                id="question-title"
                value={title}
                disabled={busy}
                maxLength={100}
                onChange={(e) => setTitle(e.target.value)}
              />
              {edited && (
                <Button disabled={!title.trim()} loading={busy} onClick={saveName}>
                  保存名称
                </Button>
              )}
              <Button type="link" disabled={busy} onClick={onCancel}>
                更换文件
              </Button>
            </div>
          )}
          <dl className="workflow-summary" aria-label="文件识别数量">
            <div>
              <dt>参评人数</dt>
              <dd>
                <strong>{view.participant_count}</strong> 人
              </dd>
            </div>
            <div>
              <dt>评分记录</dt>
              <dd>
                <strong>{view.observation_count}</strong> 条
              </dd>
            </div>
            <div>
              <dt>题目</dt>
              <dd>
                <strong>{view.recognized_items.length}</strong> 道
              </dd>
            </div>
          </dl>
          <p aria-label="评分质量分类">
            {Object.entries(qualityLabels)
              .map(
                ([key, label]) =>
                  label +
                  ' ' +
                  (view.quality_counts[key as keyof typeof qualityLabels] ?? 0) +
                  ' 条',
              )
              .join(' · ')}
          </p>
          {!confirmation && !committed && (
            <>
              {!!view.quality_issues.length && (
                <Alert
                  type="warning"
                  showIcon
                  title="部分评分缺失或不符合量表"
                  description="这些记录会保留来源，并排除在有效分数统计之外。"
                />
              )}
              <details className="panel original-records-section" open>
                <summary>题目与量表</summary>
                <Table
                  rowKey="item_id"
                  size="small"
                  pagination={false}
                  dataSource={view.recognized_items}
                  columns={[
                    { title: '题目', dataIndex: 'display_name' },
                    { title: '量表', render: (_, item) => itemScaleLabel(item.scale) },
                  ]}
                />
              </details>
              {!!view.quality_issues.length && (
                <details className="panel original-records-section">
                  <summary>查看评分问题与位置</summary>
                  <Table
                    rowKey={(issue) => issue.sheet + issue.cell}
                    size="small"
                    dataSource={view.quality_issues}
                    pagination={{ pageSize: 10, showSizeChanger: false }}
                    columns={[
                      {
                        title: '题目',
                        render: (_, issue) =>
                          view.recognized_items.find((item) => item.item_id === issue.item_id)
                            ?.display_name,
                      },
                      { title: '问题', render: (_, issue) => qualityLabels[issue.category] },
                      { title: '位置', render: (_, issue) => issue.sheet + '!' + issue.cell },
                    ]}
                  />
                </details>
              )}
            </>
          )}
        </>
      )}
    </UploadWorkflow>
  );
}
