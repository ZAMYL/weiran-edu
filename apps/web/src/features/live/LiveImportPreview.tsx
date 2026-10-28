'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, App, Button, Checkbox, Input, Pagination, Steps, Table, Tag } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import { type ApiImport, type ApiIssue } from '../../services/edu-api/types';
import { dimensionName } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';

export default function LiveImportPreview({
  initial,
  onCancel,
}: {
  initial: ApiImport;
  onCancel: () => void;
}) {
  const router = useRouter();
  const [recordsOpen, setRecordsOpen] = useState(false);
  const [issuesOpen, setIssuesOpen] = useState(false);
  const { client, batchChanged, uploadDraft, setUploadDraft } = useDataMode();
  const committed = uploadDraft?.committed;
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  const { message } = App.useApp();
  const [rowOffset, setRowOffset] = useState(0),
    [issueOffset, setIssueOffset] = useState(0);
  const [located, setLocated] = useState<string | null>(null);
  const [title, setTitle] = useState(initial.import.title);
  const [ack, setAck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const importId = initial.import.import_id;
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [view, quality] = await Promise.all([
        client.import(importId, rowOffset, issueOffset, signal),
        client.quality(importId, signal),
      ]);
      return { view, quality };
    },
    [client, importId, rowOffset, issueOffset],
  );
  const resource = useRemote(`import:${importId}:${rowOffset}:${issueOffset}`, load);
  const view = resource.data?.view,
    quality = resource.data?.quality,
    info = view?.import;
  const edited = info ? title.trim() !== info.title : false;
  const canCommit = Boolean(
    info &&
    view?.preview_available &&
    info.status === 'preview_ready' &&
    quality?.validation_passed &&
    view.summary.error_count === 0 &&
    (!quality.requires_warning_acknowledgement || ack === info.preview_version) &&
    !edited &&
    !busy,
  );
  useEffect(() => {
    if (located && resource.data)
      document
        .querySelector('.located-row')
        ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [located, resource.data]);
  function locate(issue: ApiIssue) {
    setRecordsOpen(true);
    if (issue.row_offset !== null) {
      setRowOffset(Math.floor(issue.row_offset / 10) * 10);
      setLocated(issue.row_key);
    }
  }
  async function saveTitle() {
    if (!info || !title.trim() || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setActionError(null);
    setAck(null);
    try {
      const updated = await client.patchImport(
        importId,
        info.preview_version,
        title.trim(),
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (uploadDraft)
        setUploadDraft({ ...uploadDraft, preview: updated, title: updated.import.title });
      resource.retry();
      message.success('测评名称已更新，请重新确认本次检查结果。');
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setActionError(e);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        pending.current = null;
      }
    }
  }
  async function confirm() {
    if ((!committed && (!canCommit || !info)) || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setActionError(null);
    try {
      const result =
        committed ??
        (await client.commit(
          importId,
          info!.preview_version,
          ack === info!.preview_version,
          controller.signal,
        ));
      if (controller.signal.aborted) return;
      if (uploadDraft) setUploadDraft({ ...uploadDraft, committed: result });
      const batch = await client.batch(result.batch_id, controller.signal);
      if (controller.signal.aborted) return;
      if (result.snapshot_id && result.snapshot_id !== batch.snapshot_id)
        throw new ApiError(409, 'SNAPSHOT_MISMATCH');
      batchChanged(batch);
      router.replace('/insights');
      message.success(
        result.result === 'created'
          ? '测评已确认导入。'
          : result.result === 'duplicate'
            ? '已打开相同内容的已有测评，未重复导入。'
            : '该预览已确认，已返回既有批次。',
      );
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setActionError(e);
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        pending.current = null;
      }
    }
  }
  return (
    <div className="page-stack import-preview">
      <div className="preview-heading">
        <div>
          <h2>导入预览</h2>
          <p className="muted">{title} · 文件已检查，确认后直接进入诊断。</p>
        </div>
        <Button onClick={onCancel} disabled={busy}>
          重新选择文件
        </Button>
      </div>
      <Panel>
        <Steps
          current={2}
          responsive={false}
          items={[{ title: '选择文件' }, { title: '自动检查' }, { title: '确认导入' }]}
        />
      </Panel>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {actionError && <RequestError error={actionError} retry={resource.retry} />}
      {committed && (
        <Alert
          type="success"
          showIcon
          title="文件已确认导入"
          description="正在打开测评诊断。如加载失败，可直接重试打开，无需重新上传或再次导入。"
        />
      )}
      {view && info && quality && (
        <>
          <section className="panel import-recognition">
            <h3>已识别内容</h3>
            <p>
              参评记录 {view.summary.detected_student_count} 条 · 评分记录{' '}
              {view.summary.parsed_evaluation_count} 条
            </p>
            <p className="muted small">
              以上为文件记录数，包含需要修正的记录；正式参评人数以导入后的诊断为准。
            </p>
            {!!view.recognized_items?.length && (
              <div className="recognized-items">
                {view.recognized_items.map((item) => (
                  <Tag key={item.item_id}>
                    {item.display_name} · {item.scale.minimum}–{item.scale.maximum} 分
                  </Tag>
                ))}
              </div>
            )}
          </section>
          <div className="import-preview-grid compact-import">
            <details
              className="panel original-records-section"
              open={recordsOpen}
              onToggle={(event) => setRecordsOpen(event.currentTarget.open)}
            >
              <summary>查看原始记录</summary>
              <p className="muted small">评语仅在审核后展示，未审核原文不会在此显示。</p>
              <Table
                rowKey="row_key"
                size="small"
                dataSource={view.rows ?? []}
                scroll={{ x: 740 }}
                rowClassName={(row) =>
                  `${row.issue_codes.length ? 'error-row' : ''} ${located === row.row_key ? 'located-row' : ''}`
                }
                pagination={false}
                columns={[
                  { title: '匿名学生', dataIndex: 'display_label' },
                  { title: '能力维度', dataIndex: 'dimension_code', render: dimensionName },
                  {
                    title: '原始评分',
                    dataIndex: 'score_raw',
                    render: (value, row) =>
                      value ?? (row.score_state === 'invalid' ? '非法值（不展示原文）' : '缺失'),
                  },
                  {
                    title: '来源',
                    key: 'source',
                    render: (_, row) =>
                      `${row.source.sheet_name} · 第${row.source.row_number}行 · ${row.source.score_cell}`,
                  },
                ]}
              />
              <Pagination
                current={Math.floor(rowOffset / 10) + 1}
                total={view.summary.parsed_evaluation_count}
                pageSize={10}
                showSizeChanger={false}
                onChange={(page) => setRowOffset((page - 1) * 10)}
                showTotal={(total) => `共 ${total} 条评分行`}
              />
              <details className="technical-details">
                <summary>技术详情</summary>
                <dl className="source-details">
                  <dt>预览版本</dt>
                  <dd>{info.preview_version}</dd>
                  <dt>解析器 / 评价标准版本</dt>
                  <dd>
                    {info.parser_version} / {info.rubric_version ?? '尚未确定'}
                  </dd>
                  <dt>到期时间</dt>
                  <dd>{new Date(info.expires_at).toLocaleString('zh-CN')}</dd>
                </dl>
              </details>
            </details>
            <Panel
              className="quality-report"
              title="文件检查结果"
              action={
                <Tag
                  color={
                    quality.status === 'blocked'
                      ? 'error'
                      : quality.status === 'warning'
                        ? 'warning'
                        : 'success'
                  }
                >
                  {quality.status === 'blocked'
                    ? '存在阻断错误'
                    : quality.status === 'warning'
                      ? '需要注意的问题'
                      : '校验通过'}
                </Tag>
              }
            >
              <p>
                有效评分 {quality.summary.valid_score_count} 条 · 缺失{' '}
                {quality.summary.missing_score_count} 条 · 非法{' '}
                {quality.summary.invalid_score_count} 条
              </p>
              <p className="muted small">完整报告汇总全部记录，不因当前预览页变化。</p>
              <div className="issue-groups">
                {quality.issue_groups.map((group) => (
                  <section className="issue-group" key={group.code}>
                    <h3>{group.code === 'COMMENT_REPEATED' ? '部分评语重复' : group.message}</h3>
                    <p className="muted small">
                      影响 {group.affected_student_count} 人 · {group.occurrence_count} 项
                      {group.severity === 'error' ? '错误' : '提醒'}
                    </p>
                    <p className="muted small">
                      {group.code === 'COMMENT_REPEATED'
                        ? '建议核对评语是否逐人填写；评分可正常导入，原文仍按审核权限展示。'
                        : group.severity === 'error'
                          ? '请修正涉及记录后重新上传。'
                          : '请核查涉及记录，确认后可继续导入。'}
                    </p>
                    <details>
                      <summary>查看涉及的记录</summary>
                      {group.examples.map((issue) => (
                        <div className="issue-example" key={issue.issue_id}>
                          <span>
                            {issue.sheet_name} · 第 {issue.row_number} 行 · {issue.cell}
                          </span>
                          {issue.row_offset !== null && (
                            <Button size="small" type="link" onClick={() => locate(issue)}>
                              定位记录
                            </Button>
                          )}
                        </div>
                      ))}
                      <p className="muted small">
                        以上为检查结果提供的位置示例；完整问题可在下方分页查看。
                      </p>
                    </details>
                  </section>
                ))}
              </div>
              {view.summary.error_count + view.summary.warning_count > 0 && (
                <details
                  className="all-issues"
                  open={issuesOpen}
                  onToggle={(event) => setIssuesOpen(event.currentTarget.open)}
                >
                  <summary>查看完整问题列表</summary>
                  <Table
                    rowKey="issue_id"
                    size="small"
                    pagination={false}
                    dataSource={view.issues ?? []}
                    scroll={{ x: 520 }}
                    columns={[
                      {
                        title: '类型',
                        render: (_, issue) => (issue.severity === 'error' ? '错误' : '提醒'),
                      },
                      { title: '问题', dataIndex: 'message' },
                      {
                        title: '位置',
                        render: (_, issue) =>
                          `${issue.sheet_name} · 第 ${issue.row_number} 行 · ${issue.cell}`,
                      },
                      {
                        title: '操作',
                        render: (_, issue) =>
                          issue.row_offset !== null ? (
                            <Button type="link" size="small" onClick={() => locate(issue)}>
                              定位记录
                            </Button>
                          ) : null,
                      },
                    ]}
                  />
                  <Pagination
                    size="small"
                    current={Math.floor(issueOffset / 10) + 1}
                    total={view.summary.error_count + view.summary.warning_count}
                    pageSize={10}
                    hideOnSinglePage
                    showSizeChanger={false}
                    onChange={(page) => setIssueOffset((page - 1) * 10)}
                  />
                </details>
              )}
              {!!view.summary.error_count && (
                <Alert
                  type="error"
                  showIcon
                  title="存在阻断错误，无法确认入库"
                  description="请修正原始文件后重新上传，系统不会自动改分。"
                />
              )}
              {quality.requires_warning_acknowledgement && (
                <Checkbox
                  className="warning-ack"
                  checked={ack === info.preview_version}
                  disabled={busy || Boolean(committed)}
                  onChange={(e) => setAck(e.target.checked ? info.preview_version : null)}
                >
                  我已查看以上提醒，确认继续导入
                </Checkbox>
              )}
              <div className="batch-name-field">
                <label htmlFor="live-preview-title">测评名称</label>
                <Input
                  id="live-preview-title"
                  value={title}
                  maxLength={100}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={busy}
                />
                {edited && !committed && (
                  <Button onClick={saveTitle} loading={busy} disabled={!title.trim()}>
                    保存测评名称
                  </Button>
                )}
              </div>
            </Panel>
          </div>
        </>
      )}
      <section className="panel preview-footer">
        <Button onClick={onCancel} disabled={busy}>
          返回上传
        </Button>
        <div>
          <span className="muted">
            {edited ? '先保存测评名称，再确认导入' : '确认后即可查看学情诊断'}
          </span>
          <Button
            type="primary"
            aria-label={committed ? '重新打开诊断' : '确认导入'}
            disabled={busy || (!committed && !canCommit)}
            loading={busy}
            onClick={confirm}
          >
            {committed ? '重新打开诊断' : '确认导入'}
          </Button>
        </div>
      </section>
    </div>
  );
}
