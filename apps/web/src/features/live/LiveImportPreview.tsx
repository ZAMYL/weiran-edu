'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Alert, App, Button, Checkbox, Input, Pagination, Table } from 'antd';
import { FileExcelTwoTone } from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import { type ApiImport, type ApiIssue, type ApiQuality } from '../../services/edu-api/types';
import { dimensionName } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import UploadWorkflow from './UploadWorkflow';
import { assessmentNameFromFilename } from '../../services/assessment-name';

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
  const [confirmation, setConfirmation] = useState(false);
  const confirmStep = confirmation || Boolean(committed);
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
    if (!info || pending.current || committed) return;
    const name =
      title.trim() ||
      (uploadDraft?.file ? assessmentNameFromFilename(uploadDraft.file.name) : info.title);
    setTitle(name);
    if (name === info.title) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setActionError(null);
    setAck(null);
    try {
      const updated = await client.patchImport(
        importId,
        info.preview_version,
        name,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      if (uploadDraft)
        setUploadDraft({ ...uploadDraft, preview: updated, title: updated.import.title });
      resource.retry();
      message.success(
        updated.summary.warning_count ? '名称已保存，请重新确认检查提醒。' : '测评名称已保存。',
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
    <UploadWorkflow
      step={confirmStep ? 2 : 1}
      busy={busy || resource.loading}
      actions={
        confirmStep ? (
          <>
            <Button onClick={() => setConfirmation(false)} disabled={busy || Boolean(committed)}>
              返回检查
            </Button>
            <Button
              type="primary"
              aria-label={committed ? '重新打开诊断' : '确认导入'}
              disabled={busy || (!committed && !canCommit)}
              loading={busy}
              onClick={confirm}
            >
              {committed ? '重新打开诊断' : '确认导入'}
            </Button>
          </>
        ) : (
          <>
            <div className="workflow-review-consent">
              {quality?.requires_warning_acknowledgement && info && (
                <Checkbox
                  className="warning-ack"
                  checked={ack === info.preview_version}
                  disabled={busy || Boolean(committed)}
                  onChange={(e) => setAck(e.target.checked ? info.preview_version : null)}
                >
                  我已查看以上提醒，确认继续导入
                </Checkbox>
              )}
            </div>
            <div className="workflow-next-action">
              <Button
                type="primary"
                aria-label="下一步：确认导入"
                disabled={!canCommit}
                onClick={() => setConfirmation(true)}
              >
                下一步
              </Button>
            </div>
          </>
        )
      }
    >
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
      {view && info && quality && confirmStep && (
        <section className="workflow-confirmation" aria-label="确认测评信息">
          <h2>{info.title}</h2>
          {uploadDraft?.file && (
            <div className="selected-assessment-file">
              <FileExcelTwoTone twoToneColor="#12805b" aria-hidden="true" />
              <span>{uploadDraft.file.name}</span>
            </div>
          )}
          <ImportSummary view={view} quality={quality} />
          {quality.requires_warning_acknowledgement && (
            <details className="workflow-confirm-warnings">
              <summary>
                已确认 {quality.issue_groups.filter((g) => g.severity === 'warning').length} 类提醒
              </summary>
              {quality.issue_groups
                .filter((g) => g.severity === 'warning')
                .map((group) => (
                  <p key={group.code}>
                    {group.code === 'COMMENT_REPEATED' ? '部分评语重复' : group.message} ·{' '}
                    {group.occurrence_count} 项
                  </p>
                ))}
            </details>
          )}
          <p className="muted">导入成功后，自动进入本次测评诊断。</p>
        </section>
      )}
      <div className="workflow-review" hidden={confirmStep}>
        {view && info && quality && (
          <>
            <div className="selected-assessment-file">
              <FileExcelTwoTone twoToneColor="#12805b" aria-hidden="true" />
              <div>
                <h3>{uploadDraft?.file?.name ?? '测评文件'}</h3>
              </div>
              <Button type="link" onClick={onCancel} disabled={busy}>
                更换文件
              </Button>
            </div>
            <div className="batch-name-field">
              <label htmlFor="live-preview-title">测评名称</label>
              <Input
                id="live-preview-title"
                value={title}
                maxLength={100}
                onChange={(e) => {
                  setTitle(e.target.value);
                  setAck(null);
                }}
                onBlur={() => {
                  if (edited) void saveTitle();
                }}
                onPressEnter={() => void saveTitle()}
                disabled={busy}
              />
              {edited && actionError && !committed && (
                <Button onClick={() => void saveTitle()} loading={busy}>
                  重试保存名称
                </Button>
              )}
            </div>
            <ImportSummary view={view} quality={quality} />
            <div className="import-preview-grid compact-import">
              <details
                className="panel original-records-section"
                open={recordsOpen}
                onToggle={(event) => setRecordsOpen(event.currentTarget.open)}
              >
                <summary>评分预览</summary>
                <p className="muted small">评语仅在审核后展示，未审核原文不会在此显示。</p>
                {!!view.recognized_items?.length && (
                  <details className="workflow-recognized-items">
                    <summary>评分项目与标准</summary>
                    {view.recognized_items.map((item) => (
                      <p key={item.item_id}>
                        {item.display_name} · {item.scale.minimum}–{item.scale.maximum} 分
                      </p>
                    ))}
                  </details>
                )}
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
              <Panel className="quality-report" title="文件检查结果">
                {quality.status === 'passed' && (
                  <Alert type="success" showIcon title="文件检查通过" />
                )}
                <div className="issue-groups">
                  {quality.issue_groups.map((group) => (
                    <section
                      className={`issue-group${group.severity === 'error' ? ' is-error' : ''}`}
                      key={group.code}
                    >
                      <div className="workflow-issue-heading">
                        <h3>
                          {group.code === 'COMMENT_REPEATED' ? '部分评语重复' : group.message}
                        </h3>
                        <span>
                          影响 {group.affected_student_count} 人 · {group.occurrence_count} 项
                          {group.severity === 'error' ? '错误' : '提醒'}
                        </span>
                      </div>
                      <p className="muted">
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
              </Panel>
            </div>
          </>
        )}
      </div>
    </UploadWorkflow>
  );
}

function ImportSummary({ view, quality }: { view: ApiImport; quality: ApiQuality }) {
  return (
    <>
      <dl className="workflow-summary" aria-label="文件识别数量">
        <div>
          <dt>参评记录</dt>
          <dd>
            <strong>{view.summary.detected_student_count}</strong> 条
          </dd>
        </div>
        <div>
          <dt>有效评分</dt>
          <dd>
            <strong>{quality.summary.valid_score_count}</strong> 条
          </dd>
        </div>
        <div>
          <dt>能力维度</dt>
          <dd>
            <strong>{quality.dimensions.length}</strong> 项
          </dd>
        </div>
      </dl>
      {(quality.summary.missing_score_count > 0 || quality.summary.invalid_score_count > 0) && (
        <p className="workflow-quality-counts">
          评分记录共 {view.summary.parsed_evaluation_count} 条 · 缺失{' '}
          {quality.summary.missing_score_count} 条 · 无效 {quality.summary.invalid_score_count} 条
        </p>
      )}
    </>
  );
}
