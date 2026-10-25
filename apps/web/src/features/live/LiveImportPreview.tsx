'use client';
import { useCallback, useState } from 'react';
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
  const { client, batchChanged } = useDataMode();
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
  function locate(issue: ApiIssue) {
    if (issue.row_offset !== null) {
      setRowOffset(Math.floor(issue.row_offset / 10) * 10);
      setLocated(issue.row_key);
    }
  }
  async function saveTitle() {
    if (!info || !title.trim()) return;
    setBusy(true);
    setActionError(null);
    setAck(null);
    try {
      await client.patchImport(importId, info.preview_version, title.trim());
      resource.retry();
      message.success('测评名称已更新，请基于新版本确认。');
    } catch (e) {
      if (e instanceof ApiError) setActionError(e);
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!canCommit || !info) return;
    setBusy(true);
    setActionError(null);
    try {
      const result = await client.commit(
        importId,
        info.preview_version,
        ack === info.preview_version,
      );
      const batch = await client.batch(result.batch_id);
      batchChanged(batch);
      message.success(
        result.result === 'created'
          ? '服务端已确认入库，独立批次已创建。'
          : result.result === 'duplicate'
            ? '已关联至同内容的既有批次，未重复创建。'
            : '该预览已确认，已返回既有批次。',
      );
    } catch (e) {
      if (e instanceof ApiError) setActionError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-stack import-preview">
      <div className="preview-heading">
        <div>
          <h2>正式导入预览</h2>
          <p className="muted">服务端暂存 · 原始双 Sheet 五维 Excel · 评分最多两位小数</p>
        </div>
        <Button onClick={onCancel} disabled={busy}>
          返回测评数据
        </Button>
      </div>
      <Panel>
        <Steps
          current={1}
          items={[{ title: '上传文件' }, { title: '预览校验' }, { title: '确认入库' }]}
        />
      </Panel>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {actionError && <RequestError error={actionError} retry={resource.retry} />}
      {view && info && quality && (
        <>
          <div className="preview-stats">
            {[
              ['检测到的学生块', view.summary.detected_student_count, '人'],
              ['五维完整合法', view.summary.complete_student_count, '人'],
              ['解析评分行', view.summary.parsed_evaluation_count, '条'],
              ['错误 / 警告', `${view.summary.error_count} / ${view.summary.warning_count}`, '项'],
            ].map(([label, value, unit]) => (
              <Panel className="preview-stat" key={label}>
                <p>{label}</p>
                <strong>{value}</strong>
                <span>{unit}</span>
              </Panel>
            ))}
          </div>
          <div className="import-preview-grid">
            <Panel
              title="服务端解析预览"
              subtitle="已识别行包括非法评分行；被扣留的非空原文不作为空评语显示。"
            >
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
                    title: '评语状态',
                    dataIndex: 'comment_status',
                    render: (status) => (
                      <Tag color={status === 'withheld' ? 'blue' : 'default'}>
                        {status === 'withheld' ? '非空 · 待审核' : '空评语'}
                      </Tag>
                    ),
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
            </Panel>
            <Panel
              title="服务端数据质量报告"
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
                      ? '需确认警告'
                      : '校验通过'}
                </Tag>
              }
            >
              <p>
                合法数值评分 {quality.summary.valid_score_count} 条 · 缺失{' '}
                {quality.summary.missing_score_count} 条 · 非法{' '}
                {quality.summary.invalid_score_count} 条
              </p>
              <p className="muted small">完整报告汇总全部记录，不因当前预览页变化。</p>
              <div className="issue-list">
                {view.issues?.map((issue) => (
                  <article className={`issue-card ${issue.severity}`} key={issue.issue_id}>
                    <div>
                      <h3>
                        {issue.severity === 'error' ? '错误' : '警告'} · {issue.code}
                      </h3>
                      {issue.row_offset !== null && (
                        <Button type="link" size="small" onClick={() => locate(issue)}>
                          定位记录
                        </Button>
                      )}
                    </div>
                    <p>{issue.message}</p>
                    <p>
                      {issue.sheet_name} · 第 {issue.row_number} 行 · {issue.cell}
                      {issue.display_label ? ` · ${issue.display_label}` : ''}
                    </p>
                  </article>
                ))}
              </div>
              <Pagination
                size="small"
                current={Math.floor(issueOffset / 10) + 1}
                total={view.summary.error_count + view.summary.warning_count}
                pageSize={10}
                hideOnSinglePage
                showSizeChanger={false}
                onChange={(page) => setIssueOffset((page - 1) * 10)}
              />
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
                  disabled={busy}
                  onChange={(e) => setAck(e.target.checked ? info.preview_version : null)}
                >
                  我已查看本预览版本的全部质量警告，确认继续导入
                </Checkbox>
              )}
              <div className="batch-name-field">
                <label htmlFor="live-preview-title">正式测评名称</label>
                <Input
                  id="live-preview-title"
                  value={title}
                  maxLength={100}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={busy}
                />
                {edited && (
                  <Button onClick={saveTitle} loading={busy} disabled={!title.trim()}>
                    保存名称并更新预览版本
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
            {edited ? '先保存测评名称，再确认新版本' : '确认以服务端校验与最新预览版本为准'}
          </span>
          <Button type="primary" disabled={!canCommit} loading={busy} onClick={confirm}>
            确认入库
          </Button>
        </div>
      </section>
    </div>
  );
}
