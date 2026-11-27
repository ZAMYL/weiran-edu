'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Col,
  Flex,
  Input,
  Pagination,
  Row,
  Statistic,
  Table,
  Typography,
} from 'antd';
import { FileExcelTwoTone } from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import {
  DIMENSIONS,
  type ApiImport,
  type ApiIssue,
  type ApiQuality,
} from '../../services/edu-api/types';
import { dimensionName } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import UploadWorkflow from './UploadWorkflow';
import { assessmentNameFromFilename } from '../../services/assessment-name';
import { itemScaleLabel } from '../../services/edu-api/assessment-items';

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
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(
    () => () => {
      if (pending.current) {
        pending.current.abort();
        pending.current = null;
        setBusy(false);
      }
    },
    [],
  );
  const { message } = App.useApp();
  const [rowOffset, setRowOffset] = useState(0),
    [issueOffset, setIssueOffset] = useState(0);
  const [located, setLocated] = useState<string | null>(null);
  const [title, setTitle] = useState(initial.import.title);
  const [ack, setAck] = useState<string | null>(null);
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
      const previewItems = initial.recognized_items ?? [];
      if (
        !batch.recognized_items &&
        previewItems.some(
          (item) =>
            item.kind !== 'competency' ||
            item.scale?.minimum !== 1 ||
            item.scale.maximum !== 5 ||
            !DIMENSIONS.some(({ code }) => code === item.item_id),
        )
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      if (
        batch.recognized_items &&
        previewItems.length &&
        (batch.recognized_items.length !== previewItems.length ||
          previewItems.some(
            (item) =>
              !batch.recognized_items?.some(
                (saved) => saved.item_id === item.item_id && saved.kind === item.kind,
              ),
          ))
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
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
            <Card size="small" type="inner" className="selected-assessment-file">
              <Flex align="center" gap={tokens.sizeSM}>
                <FileExcelTwoTone
                  twoToneColor={tokens.colorSuccess}
                  style={{ fontSize: 24, flex: 'none' }}
                  aria-hidden="true"
                />
                <Typography.Text strong>{uploadDraft.file.name}</Typography.Text>
              </Flex>
            </Card>
          )}
          <ImportSummary view={view} quality={quality} />
          {quality.requires_warning_acknowledgement && (
            <Disclosure
              className="workflow-confirm-warnings"
              label={
                <>
                  已确认 {quality.issue_groups.filter((g) => g.severity === 'warning').length}{' '}
                  类提醒
                </>
              }
            >
              {quality.issue_groups
                .filter((g) => g.severity === 'warning')
                .map((group) => (
                  <p key={group.code}>
                    {group.code === 'COMMENT_REPEATED' ? '部分评语重复' : group.message} ·{' '}
                    {group.occurrence_count} 项
                  </p>
                ))}
            </Disclosure>
          )}
          <Typography.Text type="secondary">导入成功后，自动进入本次测评诊断。</Typography.Text>
        </section>
      )}
      <div className="workflow-review" hidden={confirmStep}>
        {view && info && quality && (
          <Flex vertical gap={tokens.size}>
            <Card size="small" type="inner" className="selected-assessment-file">
              <Flex align="center" gap={tokens.sizeSM}>
                <FileExcelTwoTone
                  twoToneColor={tokens.colorSuccess}
                  style={{ fontSize: 28, flex: 'none' }}
                  aria-hidden="true"
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Typography.Title level={5} style={{ margin: 0, overflowWrap: 'anywhere' }}>
                    {uploadDraft?.file?.name ?? '测评文件'}
                  </Typography.Title>
                </div>
                <Button type="link" onClick={onCancel} disabled={busy}>
                  更换文件
                </Button>
              </Flex>
            </Card>
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
              {!!view.recognized_items?.length && (
                <Disclosure
                  className="panel original-records-section workflow-recognized-items"
                  label={<>评分项目与量表</>}
                >
                  <ul>
                    {view.recognized_items.map((item) => (
                      <li key={item.item_id}>
                        {item.display_name} · {itemScaleLabel(item.scale)}
                      </li>
                    ))}
                  </ul>
                </Disclosure>
              )}
              <Disclosure
                className="panel original-records-section"
                expanded={recordsOpen}
                onExpandedChange={setRecordsOpen}
                label={<>评分预览</>}
              >
                <p className="muted small">评语仅在审核后展示，未审核原文不会在此显示。</p>
                <Table
                  rowKey="row_key"
                  dataSource={view.rows ?? []}
                  scroll={{ x: 'max-content' }}
                  rowClassName={(row) =>
                    `${row.issue_codes.length ? 'error-row' : ''} ${located === row.row_key ? 'located-row' : ''}`
                  }
                  pagination={false}
                  columns={[
                    { title: '学生标识', dataIndex: 'display_label' },
                    {
                      title: '评分项目',
                      render: (_, row) =>
                        view.recognized_items?.find(
                          (item) => item.item_id === (row.item_id ?? row.dimension_code),
                        )?.display_name ??
                        (row.item_id ? '未识别项目' : dimensionName(row.dimension_code)),
                    },
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
                <Disclosure className="technical-details" label={<>错误详情</>}>
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
                </Disclosure>
              </Disclosure>
              <Panel className="quality-report" title="文件检查结果" variant="borderless">
                <Flex vertical gap={tokens.sizeSM}>
                  {quality.status === 'passed' && (
                    <Alert type="success" showIcon title="文件检查通过" />
                  )}
                  <Flex vertical gap={tokens.sizeSM} className="issue-groups">
                    {quality.issue_groups.map((group) => (
                      <Alert
                        key={group.code}
                        className={`issue-group${group.severity === 'error' ? ' is-error' : ''}`}
                        type={group.severity === 'error' ? 'error' : 'warning'}
                        showIcon
                        title={
                          <Flex justify="space-between" align="baseline" wrap gap={tokens.sizeSM}>
                            <span>
                              {group.code === 'COMMENT_REPEATED' ? '部分评语重复' : group.message}
                            </span>
                            <Typography.Text
                              type="secondary"
                              style={{ fontSize: 12, fontWeight: 400 }}
                            >
                              影响 {group.affected_student_count} 人 · {group.occurrence_count} 项
                              {group.severity === 'error' ? '错误' : '提醒'}
                            </Typography.Text>
                          </Flex>
                        }
                        description={
                          <Flex vertical gap={tokens.sizeXS}>
                            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                              {group.code === 'COMMENT_REPEATED'
                                ? '建议核对评语是否逐人填写；评分可正常导入，原文仍按审核权限展示。'
                                : group.severity === 'error'
                                  ? '请修正涉及记录后重新上传。'
                                  : '请核查涉及记录，确认后可继续导入。'}
                            </Typography.Text>
                            <Disclosure label={<>查看涉及的记录</>}>
                              {group.examples.map((issue) => (
                                <Flex
                                  justify="space-between"
                                  align="center"
                                  wrap
                                  gap={tokens.sizeXS}
                                  className="issue-example"
                                  key={issue.issue_id}
                                >
                                  <span>
                                    {issue.sheet_name} · 第 {issue.row_number} 行 · {issue.cell}
                                  </span>
                                  {issue.row_offset !== null && (
                                    <Button size="small" type="link" onClick={() => locate(issue)}>
                                      定位记录
                                    </Button>
                                  )}
                                </Flex>
                              ))}
                              <Typography.Text
                                type="secondary"
                                style={{ fontSize: 12, display: 'block', marginTop: tokens.sizeXS }}
                              >
                                以上为检查结果提供的位置示例；完整问题可在下方分页查看。
                              </Typography.Text>
                            </Disclosure>
                          </Flex>
                        }
                      />
                    ))}
                  </Flex>
                  {view.summary.error_count + view.summary.warning_count > 0 && (
                    <Disclosure
                      className="all-issues"
                      expanded={issuesOpen}
                      onExpandedChange={setIssuesOpen}
                      label={<>查看完整问题列表</>}
                    >
                      <Table
                        rowKey="issue_id"
                        pagination={false}
                        dataSource={view.issues ?? []}
                        scroll={{ x: 'max-content' }}
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
                    </Disclosure>
                  )}
                  {!!view.summary.error_count && (
                    <Alert
                      type="error"
                      showIcon
                      title="存在阻断错误，无法确认入库"
                      description="请修正原始文件后重新上传，系统不会自动改分。"
                    />
                  )}
                </Flex>
              </Panel>
            </div>
          </Flex>
        )}
      </div>
    </UploadWorkflow>
  );
}

function ImportSummary({ view, quality }: { view: ApiImport; quality: ApiQuality }) {
  return (
    <Flex vertical gap={tokens.sizeXS}>
      <Row
        gutter={[tokens.size, tokens.size]}
        className="workflow-summary"
        role="group"
        aria-label="文件识别数量"
      >
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="参评记录"
              value={view.summary.detected_student_count}
              suffix="条"
              valueRender={(node) => <strong>{node}</strong>}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="有效评分"
              value={quality.summary.valid_score_count}
              suffix="条"
              valueRender={(node) => <strong>{node}</strong>}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small">
            <Statistic
              title="评分项目"
              value={view.recognized_items?.length ?? quality.dimensions.length}
              suffix="项"
              valueRender={(node) => <strong>{node}</strong>}
            />
          </Card>
        </Col>
      </Row>
      {(quality.summary.missing_score_count > 0 || quality.summary.invalid_score_count > 0) && (
        <Typography.Text
          type="secondary"
          className="workflow-quality-counts"
          style={{ fontSize: 13 }}
        >
          评分记录共 {view.summary.parsed_evaluation_count} 条 · 缺失{' '}
          {quality.summary.missing_score_count} 条 · 无效 {quality.summary.invalid_score_count} 条
        </Typography.Text>
      )}
    </Flex>
  );
}
