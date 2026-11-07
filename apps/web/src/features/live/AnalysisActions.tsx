'use client';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import type { DrawerProps } from 'antd';
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  Modal,
  Pagination,
  Radio,
  Segmented,
  Tag,
} from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  DownOutlined,
  EditOutlined,
  ExportOutlined,
} from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import {
  allowed,
  decisionLabels,
  type AnalysisResult,
  type DecisionStatus,
  type FindingDecision,
  type ReportFormat,
  type DiscoverySelection,
} from '../../services/edu-api/analysis-types';
import { saveReportFile } from '../../services/edu-api/report-file';
import type { ApiBatch, InsightSpec } from '../../services/edu-api/types';
import { conditionText, metricDisplay, ratioDisplay } from '../../services/edu-api/view-model';
import { MetricEvidenceDrawer, type MetricSelection } from './LiveStudents';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';
import { DiscoveryDetailDrawer } from './LiveDiscoveries';
import { discoverySummaryText } from '../../services/edu-api/discovery-view';

function AnalysisSurface({
  inline,
  heading,
  actions,
  workspace,
  ...props
}: DrawerProps & {
  inline?: boolean;
  heading?: ReactNode;
  actions?: ReactNode;
  workspace?: boolean;
}) {
  if (inline && workspace)
    return (
      <section
        role="region"
        aria-label={typeof props.title === 'string' ? props.title : '报告结果'}
        className="report-surface report-editor-surface"
      >
        <header className="report-top-toolbar">{actions}</header>
        {props.children}
      </section>
    );
  return inline ? (
    <section
      role="region"
      aria-label={typeof props.title === 'string' ? props.title : '报告结果'}
      className="report-surface"
    >
      <Panel
        title={heading ?? props.title}
        action={
          actions ??
          (props.onClose ? (
            <Button disabled={props.closable === false} onClick={props.onClose}>
              返回草稿
            </Button>
          ) : undefined)
        }
      >
        {props.children}
      </Panel>
    </section>
  ) : (
    <Drawer {...props} />
  );
}
// Mutations are cancelled with the containing batch/session UI, including streamed downloads.
function useOperation() {
  const pending = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => () => pending.current?.abort(), []);
  async function run(work: (signal: AbortSignal) => Promise<void>) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      await work(controller.signal);
    } catch (error) {
      if (!controller.signal.aborted)
        setError(error instanceof ApiError ? error : new ApiError(502, 'INVALID_API_RESPONSE'));
    } finally {
      if (!controller.signal.aborted) {
        setBusy(false);
        pending.current = null;
      }
    }
  }
  return { busy, error, run };
}

export default function AnalysisActions({
  batch,
  spec,
  blocked,
  discovery,
  discoveryNames,
  draftContent,
}: {
  batch: ApiBatch;
  spec: InsightSpec;
  blocked?: string;
  discovery?: DiscoverySelection;
  discoveryNames?: { id: string; title: string }[];
  draftContent?: ReactNode;
}) {
  const session = useDataMode();
  const { client } = session;
  const router = useRouter();
  const reportPage = usePathname() === '/reports';
  const prepare = (showSaved = false) => {
    session.setActiveReport(null);
    session.setReportDraft({
      key: `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(spec.conditions)}`,
      spec,
      discovery,
      names:
        discoveryNames ??
        (session.reportDraft?.key ===
        `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(spec.conditions)}`
          ? session.reportDraft.names
          : []),
      showSaved,
    });
    router.push('/reports');
  };
  const [save, setSave] = useState(false);
  const [history, setHistory] = useState<{ resultId: string | null } | null>(() =>
    reportPage &&
    session.activeReport?.batchId === batch.batch_id &&
    session.activeReport.snapshotId === batch.snapshot_id
      ? { resultId: session.activeReport.resultId }
      : reportPage && (session.reportDraft?.showSaved || !session.reportDraft)
        ? { resultId: null }
        : null,
  );
  const load = useCallback(
    (signal: AbortSignal) => client.capabilities(batch, signal),
    [client, batch],
  );
  const permissions = useRemote(
    `analysis-capabilities:${batch.batch_id}:${batch.snapshot_id}`,
    load,
  );
  return (
    <>
      {(!reportPage || !history) && (
        <div className={reportPage ? 'report-draft-workspace' : undefined}>
          {!history && draftContent}
          <section className="panel analysis-actions">
            {!reportPage && <h2>分析报告</h2>}
            <div className="batch-actions">
              <Button
                type="primary"
                disabled={Boolean(blocked) || !allowed(permissions.data, 'save_analysis')}
                onClick={() => (reportPage ? setSave(true) : prepare())}
              >
                {reportPage ? '保存本次分析' : '准备分析报告'}
              </Button>
              <Button onClick={() => (reportPage ? setHistory({ resultId: null }) : prepare(true))}>
                已保存分析
              </Button>
            </div>
            {blocked && <p className="muted small">{blocked}</p>}
            {permissions.error && (
              <RequestError error={permissions.error} retry={permissions.retry} />
            )}
            {permissions.data && !allowed(permissions.data, 'save_analysis') && (
              <p className="muted small">当前权限可查看已保存分析；保存和核验需批次所有者权限。</p>
            )}
          </section>
        </div>
      )}
      {save && (
        <SaveAnalysis
          batch={batch}
          spec={spec}
          discovery={discovery}
          scopeOnly={reportPage && !discovery}
          onClose={() => setSave(false)}
          onSaved={(result) => {
            setSave(false);
            session.setReportDraft(null);
            session.setActiveReport({
              batchId: batch.batch_id,
              snapshotId: batch.snapshot_id,
              resultId: result.result_id,
            });
            setHistory({ resultId: result.result_id });
          }}
        />
      )}
      {history && (
        <SavedAnalyses
          batch={batch}
          initialResultId={history.resultId}
          inline={reportPage}
          onClose={() => {
            session.setActiveReport(null);
            setHistory(null);
          }}
        />
      )}
    </>
  );
}

function SaveAnalysis({
  batch,
  spec,
  discovery,
  scopeOnly = false,
  onClose,
  onSaved,
}: {
  batch: ApiBatch;
  spec: InsightSpec;
  discovery?: DiscoverySelection;
  scopeOnly?: boolean;
  onClose: () => void;
  onSaved: (result: AnalysisResult) => void;
}) {
  const { client } = useDataMode();
  const [title, setTitle] = useState(() => `${batch.title} · 学情分析`.slice(0, 100));
  const [note, setNote] = useState('');
  const operation = useOperation();
  return (
    <Modal
      title="保存本次分析"
      open
      onCancel={onClose}
      closable={!operation.busy}
      maskClosable={false}
      cancelButtonProps={{ disabled: operation.busy }}
      confirmLoading={operation.busy}
      okText="保存分析"
      okButtonProps={{ disabled: !title.trim() }}
      onOk={() =>
        operation.run(async (signal) => {
          const result = await client.createAnalysis(
            batch,
            {
              ...spec,
              ...(scopeOnly ? { candidate_ids: [] } : {}),
              ...(discovery ? { include_dimensions: false, discovery } : {}),
              title: title.trim(),
              note,
            },
            signal,
          );
          if (!signal.aborted) onSaved(result);
        })
      }
    >
      <p>分析范围：{conditionText(spec.conditions)}</p>
      <p className="muted small">
        {discovery
          ? `已选择 ${discovery.discovery_ids.length} 项发现；未选择时仅保存范围概况`
          : '仅保存测评概况，不自动加入未选择的发现'}
        ；完整统计与所选内容将在保存时核验，不受当前分页影响。
      </p>
      <div className="analysis-fields">
        <label htmlFor="save-analysis-title">分析名称</label>
        <Input
          id="save-analysis-title"
          value={title}
          maxLength={100}
          disabled={operation.busy}
          onChange={(e) => setTitle(e.target.value)}
        />
        <label htmlFor="save-analysis-note">分析备注（可选）</label>
        <Input.TextArea
          id="save-analysis-note"
          value={note}
          maxLength={2000}
          disabled={operation.busy}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      {operation.error && <RequestError error={operation.error} />}
    </Modal>
  );
}

function SavedAnalyses({
  batch,
  inline = false,
  initialResultId,
  onClose,
}: {
  batch: ApiBatch;
  inline?: boolean;
  initialResultId: string | null;
  onClose: () => void;
}) {
  const { client, setActiveReport } = useDataMode();
  const [resultId, setResultId] = useState(initialResultId);
  const [offset, setOffset] = useState(0);
  const loadList = useCallback(
    (signal: AbortSignal) => client.analyses(batch, offset, signal),
    [client, batch, offset],
  );
  const list = useRemote(
    resultId ? null : `saved-list:${batch.batch_id}:${batch.snapshot_id}:${offset}`,
    loadList,
  );
  if (resultId)
    return (
      <AnalysisDetail
        inline={inline}
        batch={batch}
        resultId={resultId}
        onClose={onClose}
        onBack={() => {
          setActiveReport(null);
          setResultId(null);
          list.retry();
        }}
      />
    );
  return (
    <AnalysisSurface
      inline={inline}
      title="已保存分析"
      open
      onClose={onClose}
      size="min(760px, 100vw)"
    >
      <p className="muted">当前测评：{batch.title}</p>
      <div className="batch-actions">
        <Button onClick={list.retry}>刷新分析列表</Button>
        {inline && (
          <Button type="primary" onClick={onClose}>
            新建分析报告
          </Button>
        )}
      </div>
      {list.loading && <LoadingPanel />}
      {list.error && <RequestError error={list.error} retry={list.retry} />}
      {list.data && (
        <>
          {!list.data.items.length && <Empty description="本次测评还没有保存的分析。" />}
          <div className="saved-analysis-list">
            {list.data.items.map((item) => (
              <Panel
                key={item.result_id}
                title={item.title}
                subtitle={`已保存 · ${new Date(item.updated_at).toLocaleString('zh-CN')}`}
                action={
                  <Button
                    onClick={() => {
                      setActiveReport({
                        batchId: batch.batch_id,
                        snapshotId: batch.snapshot_id,
                        resultId: item.result_id,
                      });
                      setResultId(item.result_id);
                    }}
                  >
                    查看与核验
                  </Button>
                }
              >
                {null}
              </Panel>
            ))}
          </div>
          <Pagination
            current={Math.floor(offset / 10) + 1}
            total={list.data.total_count}
            pageSize={10}
            showSizeChanger={false}
            hideOnSinglePage
            onChange={(page) => setOffset((page - 1) * 10)}
            showTotal={(total) => `共 ${total} 次分析`}
          />
        </>
      )}
    </AnalysisSurface>
  );
}

function AnalysisDetail({
  batch,
  inline = false,
  resultId,
  onClose,
  onBack,
}: {
  batch: ApiBatch;
  inline?: boolean;
  resultId: string;
  onClose: () => void;
  onBack: () => void;
}) {
  const { client } = useDataMode();
  const load = useCallback(
    async (signal: AbortSignal) => {
      const [result, permissions] = await Promise.all([
        client.analysis(batch, resultId, signal),
        client.capabilities(batch, signal),
      ]);
      return { result, permissions };
    },
    [client, batch, resultId],
  );
  const resource = useRemote(
    `saved-detail:${batch.batch_id}:${batch.snapshot_id}:${resultId}`,
    load,
  );
  if (resource.data)
    return (
      <AnalysisEditor
        inline={inline}
        key={`${resultId}:${resource.data.result.version}`}
        batch={batch}
        initial={resource.data.result}
        editable={allowed(resource.data.permissions, 'edit_analysis')}
        reload={resource.retry}
        onClose={onClose}
        onBack={onBack}
      />
    );
  return (
    <AnalysisSurface
      inline={inline}
      title="已保存分析"
      open
      onClose={onClose}
      size="min(900px, 100vw)"
    >
      <Button onClick={onBack}>返回分析列表</Button>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
    </AnalysisSurface>
  );
}

function AnalysisEditor({
  batch,
  inline = false,
  initial,
  editable,
  reload,
  onClose,
  onBack,
}: {
  batch: ApiBatch;
  inline?: boolean;
  initial: AnalysisResult;
  editable: boolean;
  reload: () => void;
  onClose: () => void;
  onBack: () => void;
}) {
  const { client } = useDataMode();
  const [modal, contextHolder] = Modal.useModal();
  const [title, setTitle] = useState(initial.title);
  const [note, setNote] = useState(initial.note);
  const reviewItems = [
    ...initial.findings.map(({ candidate, teacher_review }) => ({
      id: candidate.candidate_id,
      title: candidate.title,
      summary: discoverySummaryText(candidate.summary),
      denominator: candidate.denominator,
      limitations: candidate.limitations,
      teacher_review,
      candidate,
      discovery: null,
    })),
    ...initial.discovery_findings.map(({ discovery, teacher_review }) => ({
      id: discovery.discovery_id,
      title: discovery.title,
      summary: discoverySummaryText(discovery.summary),
      denominator: discovery.scope.scope_record_count,
      limitations: discovery.limitations,
      teacher_review,
      candidate: null,
      discovery,
    })),
  ];
  const [decisions, setDecisions] = useState<FindingDecision[]>(() =>
    reviewItems.map((finding) => ({ ...finding.teacher_review })),
  );
  const [formalDetail, setFormalDetail] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string[]>(() =>
    reviewItems[0] ? [reviewItems[0].id] : [],
  );
  const [exporting, setExporting] = useState(false);
  const [selection, setSelection] = useState<MetricSelection | null>(null);
  const operation = useOperation();
  const changes = decisions.filter((decision) => {
    const previous = reviewItems.find(
      (finding) => finding.id === decision.candidate_id,
    )?.teacher_review;
    return previous?.status !== decision.status || previous.note !== decision.note;
  });
  const dirty = title !== initial.title || note !== initial.note || changes.length > 0;
  const leave = (next: () => void) => {
    if (!dirty) {
      next();
      return;
    }
    modal.confirm({
      title: '存在未保存的核验修改',
      content: '离开将放弃当前修改。',
      okText: '放弃修改',
      cancelText: '继续核验',
      onOk: next,
    });
  };
  function update(candidateId: string, patch: Partial<Pick<FindingDecision, 'status' | 'note'>>) {
    setDecisions((previous) =>
      previous.map((decision) =>
        decision.candidate_id === candidateId ? { ...decision, ...patch } : decision,
      ),
    );
  }
  return (
    <AnalysisSurface
      inline={inline}
      title="分析核验"
      heading={initial.title}
      workspace
      actions={
        inline ? (
          <>
            <Button
              type="link"
              icon={<ArrowLeftOutlined aria-hidden="true" />}
              disabled={operation.busy}
              onClick={() => leave(onBack)}
            >
              返回报告列表
            </Button>
            <button
              className="report-title-edit"
              disabled={!editable || operation.busy}
              onClick={() => document.getElementById('review-analysis-title')?.focus()}
            >
              {title}
              <EditOutlined aria-hidden="true" />
            </button>
            <span className={`report-save-state${dirty ? ' is-dirty' : ''}`}>
              <CheckCircleOutlined aria-hidden="true" />
              {dirty ? '未保存修改' : '已保存'}
            </span>
            <Button
              className="report-toolbar-export"
              type="primary"
              icon={<ExportOutlined aria-hidden="true" />}
              disabled={dirty || operation.busy}
              onClick={() => setExporting(true)}
            >
              导出报告
            </Button>
          </>
        ) : undefined
      }
      open
      onClose={() => leave(onClose)}
      maskClosable={!operation.busy}
      closable={!operation.busy}
      keyboard={!operation.busy}
      size="min(900px, 100vw)"
    >
      {contextHolder}
      {!inline && (
        <div className="batch-actions report-editor-toolbar">
          <Button disabled={operation.busy} onClick={() => leave(onBack)}>
            返回分析列表
          </Button>
          <Button disabled={operation.busy} onClick={() => leave(reload)}>
            加载最新版本
          </Button>
        </div>
      )}
      <div className="report-editor-grid">
        <aside className="report-editor-info" aria-label="报告信息">
          <h2>报告信息</h2>
          <div className="analysis-fields">
            <label htmlFor="review-analysis-title">报告名称</label>
            <Input
              id="review-analysis-title"
              value={title}
              maxLength={100}
              disabled={!editable || operation.busy}
              onChange={(e) => setTitle(e.target.value)}
            />
            <label htmlFor="review-analysis-note">备注（可选）</label>
            <Input.TextArea
              id="review-analysis-note"
              aria-label="分析备注"
              classNames={{ root: 'report-note-input', count: 'report-note-count' }}
              value={note}
              autoSize={{ minRows: 2, maxRows: 5 }}
              maxLength={2000}
              showCount
              placeholder="添加对本报告的说明（可选）"
              disabled={!editable || operation.busy}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <dl className="report-metadata">
            <div>
              <dt>测评</dt>
              <dd>{batch.title}</dd>
            </div>
            <div>
              <dt>分析范围</dt>
              <dd>{conditionText(initial.analysis.query.conditions)}</dd>
            </div>
            <div>
              <dt>群体人数</dt>
              <dd>
                {initial.analysis.matched_count} / {initial.analysis.total_count} 人
              </dd>
            </div>
            <div>
              <dt>占比</dt>
              <dd>{ratioDisplay(initial.analysis.ratio.value)}</dd>
            </div>
          </dl>
          <p className="saved-version">已保存版本 {initial.version}</p>
          <details className="technical-details">
            <summary>保存详情</summary>
            <p>
              测评快照：<code>{initial.snapshot_id}</code>
            </p>
            <p className="muted small">{initial.notice}</p>
            <div className="batch-actions">
              <Button disabled={operation.busy} onClick={() => leave(reload)}>
                加载最新版本
              </Button>
              <Button disabled={operation.busy} onClick={() => leave(onClose)}>
                返回草稿
              </Button>
            </div>
          </details>
          {!editable && (
            <Alert type="info" title="当前为只读权限，可查看核验结果和导出测评报告。" showIcon />
          )}
          <div className="review-progress" aria-label="核验进度">
            <h3>发现核验进度</h3>
            {Object.entries(decisionLabels).map(([status, label]) => (
              <div key={status} className={`review-progress-count ${status}`}>
                <span>{label}</span>
                <strong>{decisions.filter((decision) => decision.status === status).length}</strong>
              </div>
            ))}
          </div>
        </aside>
        <div className="report-editor-findings">
          <div className="review-section-heading">
            <h2>发现核验</h2>
            <span className="muted small">仅已确认发现进入报告</span>
          </div>
          {!reviewItems.length && (
            <Empty description="本次分析没有保存候选发现，仍可导出测评报告。" />
          )}
          {reviewItems.map((item, index) => {
            const candidate = item.candidate;
            const decision = decisions[index]!;
            return (
              <details
                className="review-finding"
                key={item.id}
                open={expanded.includes(item.id)}
                onToggle={(event) => {
                  const open = event.currentTarget.open;
                  setExpanded((previous) =>
                    open
                      ? previous.includes(item.id)
                        ? previous
                        : [...previous, item.id]
                      : previous.includes(item.id)
                        ? previous.filter((id) => id !== item.id)
                        : previous,
                  );
                }}
              >
                <summary className="review-finding-heading">
                  <span className="review-finding-index">{String(index + 1).padStart(2, '0')}</span>
                  <h3>{item.title}</h3>
                  <Tag
                    color={
                      decision.status === 'confirmed'
                        ? 'green'
                        : decision.status === 'dismissed'
                          ? 'default'
                          : 'gold'
                    }
                  >
                    {decisionLabels[decision.status]}
                  </Tag>
                  <DownOutlined className="review-chevron" aria-hidden="true" />
                </summary>
                <div className="review-finding-content">
                  <p className="muted small">统计范围 {item.denominator} 人</p>
                  <p>{item.summary}</p>
                  <div className="batch-actions">
                    {candidate?.metrics
                      .filter((metric) => metric.key === 'mean' || metric.key === 'matched_count')
                      .map((metric) => (
                        <Button
                          key={metric.key}
                          onClick={() =>
                            setSelection({
                              candidateId: item.id,
                              metricKey: metric.key,
                              title: item.title,
                              spec: initial.analysis.query,
                            })
                          }
                        >
                          查看评分证据 · {metricDisplay(metric)}
                        </Button>
                      ))}
                    {item.discovery && (
                      <Button onClick={() => setFormalDetail(item.id)}>
                        查看完整发现与评分依据
                      </Button>
                    )}
                  </div>
                  <label className="review-decision-label" id={`decision-${index}`}>
                    教师判断<span className="visually-hidden"> · {item.title}</span>
                  </label>
                  <Radio.Group
                    className="report-decision-options"
                    aria-labelledby={`decision-${index}`}
                    options={Object.entries(decisionLabels).map(([value, label]) => ({
                      value,
                      label: (
                        <span>
                          <i className={`decision-dot ${value}`} aria-hidden="true" />
                          {label}
                        </span>
                      ),
                    }))}
                    optionType="button"
                    value={decision.status}
                    disabled={!editable || operation.busy}
                    onChange={(event) =>
                      update(item.id, { status: event.target.value as DecisionStatus })
                    }
                  />
                  <label htmlFor={`finding-note-${index}`}>
                    核验备注<span className="visually-hidden"> · {item.title}</span>
                  </label>
                  <Input.TextArea
                    id={`finding-note-${index}`}
                    classNames={{ root: 'report-note-input', count: 'report-note-count' }}
                    value={decision.note}
                    maxLength={2000}
                    showCount
                    placeholder="添加核验备注（可选）"
                    autoSize={{ minRows: 2, maxRows: 5 }}
                    disabled={!editable || operation.busy}
                    onChange={(e) => update(item.id, { note: e.target.value })}
                  />
                  <details>
                    <summary>使用限制</summary>
                    {item.limitations.map((text) => (
                      <p className="muted small" key={text}>
                        {text}
                      </p>
                    ))}
                  </details>
                </div>
              </details>
            );
          })}
        </div>
      </div>
      {operation.error && <RequestError error={operation.error} />}
      <div className="report-action-bar">
        <span className="muted small">
          {dirty ? '先保存核验结果，再导出报告' : '已保存，可导出当前版本'}
        </span>
        {editable && (
          <Button
            type="primary"
            className="save-review-button"
            loading={operation.busy}
            disabled={!dirty || !title.trim()}
            onClick={() =>
              operation.run(async (signal) => {
                await client.patchAnalysis(
                  batch,
                  initial.result_id,
                  {
                    snapshot_id: initial.snapshot_id,
                    expected_version: initial.version,
                    title: title.trim(),
                    note,
                    decisions: changes,
                  },
                  signal,
                );
                if (!signal.aborted) reload();
              })
            }
          >
            保存核验结果
          </Button>
        )}
        <Button
          type="primary"
          disabled={dirty || operation.busy}
          onClick={() => setExporting(true)}
        >
          导出测评报告
        </Button>
      </div>
      {formalDetail && initial.analysis.discovery && (
        <DiscoveryDetailDrawer
          batch={batch}
          spec={initial.analysis.discovery.spec}
          discoveryId={formalDetail}
          allowExplore={false}
          onClose={() => setFormalDetail(null)}
        />
      )}
      {exporting && (
        <ReportExport
          batch={batch}
          result={initial}
          onClose={() => setExporting(false)}
          onReload={() => {
            setExporting(false);
            reload();
          }}
        />
      )}
      <MetricEvidenceDrawer
        key={`${selection?.candidateId}:${selection?.metricKey}`}
        batch={batch}
        selection={selection}
        onClose={() => setSelection(null)}
      />
    </AnalysisSurface>
  );
}

function ReportExport({
  batch,
  result,
  onClose,
  onReload,
}: {
  batch: ApiBatch;
  result: AnalysisResult;
  onClose: () => void;
  onReload: () => void;
}) {
  const [format, setFormat] = useState<ReportFormat>('xlsx');
  return (
    <Modal title="导出测评报告" open onCancel={onClose} footer={null} maskClosable={false}>
      <Segmented
        aria-label="报告格式"
        options={[
          { label: 'XLSX', value: 'xlsx' },
          { label: 'Markdown', value: 'md' },
        ]}
        value={format}
        onChange={setFormat}
      />
      <ReportDownload
        key={format}
        batch={batch}
        result={result}
        format={format}
        onReload={onReload}
      />
    </Modal>
  );
}

function ReportDownload({
  batch,
  result,
  format,
  onReload,
}: {
  batch: ApiBatch;
  result: AnalysisResult;
  format: ReportFormat;
  onReload: () => void;
}) {
  const { client } = useDataMode();
  const [acknowledged, setAcknowledged] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const operation = useOperation();
  const load = useCallback(
    async (signal: AbortSignal) => {
      const permissions = await client.capabilities(batch, signal);
      if (!allowed(permissions, 'export_analysis')) throw new ApiError(403, 'FORBIDDEN');
      return client.exportCheck(batch, result.result_id, result.version, format, signal);
    },
    [client, batch, result.result_id, result.version, format],
  );
  const check = useRemote(`report-check:${result.result_id}:${result.version}:${format}`, load);
  const blocked = operation.error || check.error;
  return (
    <div className="report-check">
      <p>
        分析：{result.title} · 版本 {result.version}
      </p>
      <p className="muted small">
        报告包含汇总统计与教师已确认的发现；不包含逐学生明细和自由填写的备注。仅供授权教师使用。
      </p>
      {check.loading && <LoadingPanel />}
      {check.error && <RequestError error={check.error} retry={check.retry} />}
      {check.data && (
        <>
          <p>
            分析群体 {check.data.member_count} 人 · 已确认发现 {check.data.confirmed_finding_count}{' '}
            条
          </p>
          <p className="muted small">
            报告预估大小 {(check.data.report_payload_bytes / 1024).toFixed(1)} KiB
          </p>
          {check.data.warnings.length > 0 && (
            <>
              <Alert
                type="warning"
                showIcon
                title="导出预检提醒"
                description={
                  <ul>
                    {check.data.warnings.map((warning) => (
                      <li key={warning.code}>
                        {warning.message}（{warning.count}）
                      </li>
                    ))}
                  </ul>
                }
              />
              <Checkbox checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)}>
                我已了解以上提醒，按当前核验结果导出
              </Checkbox>
            </>
          )}
        </>
      )}
      {operation.error && <RequestError error={operation.error} />}
      {blocked && <Button onClick={onReload}>关闭并加载最新结果</Button>}
      {downloaded && <Alert type="success" showIcon title="报告已下载" />}
      <Button
        type="primary"
        loading={operation.busy}
        disabled={
          !check.data || Boolean(blocked) || (check.data.warnings.length > 0 && !acknowledged)
        }
        onClick={() =>
          operation.run(async (signal) => {
            const file = await client.report(
              batch,
              result.result_id,
              result.version,
              format,
              signal,
            );
            if (!signal.aborted) {
              saveReportFile(file);
              setDownloaded(true);
            }
          })
        }
      >
        下载 {format === 'xlsx' ? 'XLSX' : 'Markdown'}
      </Button>
    </div>
  );
}
