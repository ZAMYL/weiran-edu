'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Button,
  Checkbox,
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
} from '@ant-design/icons';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import {
  allowed,
  type AnalysisResult,
  type Capabilities,
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
import type { DiscoverySpec, DiscoverySummary } from '../../services/edu-api/discovery-types';

const reportDecisions = [
  { value: 'confirmed', label: '已确认' },
  { value: 'dismissed', label: '不加入报告' },
] as const;

interface ReportPreview {
  spec: DiscoverySpec;
  discovery: DiscoverySelection;
  items: DiscoverySummary[];
  matched: number;
  total: number;
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
}: {
  batch: ApiBatch;
  spec: InsightSpec;
  blocked?: string;
  discovery?: DiscoverySelection;
}) {
  const { setReportDraft } = useDataMode();
  const router = useRouter();
  const prepare = (showSaved = false) => {
    setReportDraft({
      key: reportContextKey(batch, spec.conditions),
      spec,
      discovery,
      showSaved,
    });
    router.push('/reports');
  };
  const permissions = useReportPermissions(batch);
  return (
    <section className="panel analysis-actions">
      <h2>分析报告</h2>
      <div className="batch-actions">
        <Button
          type="primary"
          disabled={Boolean(blocked) || !allowed(permissions.data, 'save_analysis')}
          onClick={() => prepare()}
        >
          准备分析报告
        </Button>
        <Button onClick={() => prepare(true)}>已保存分析</Button>
      </div>
      {blocked && <p className="muted small">{blocked}</p>}
      {permissions.error && <RequestError error={permissions.error} retry={permissions.retry} />}
      {permissions.data && !allowed(permissions.data, 'save_analysis') && (
        <p className="muted small">当前权限可查看已保存分析；保存和核验需批次所有者权限。</p>
      )}
    </section>
  );
}

export function reportContextKey(batch: ApiBatch, conditions: InsightSpec['conditions']) {
  return `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
}

function useReportPermissions(batch: ApiBatch) {
  const { client } = useDataMode();
  const load = useCallback(
    (signal: AbortSignal) => client.capabilities(batch, signal),
    [client, batch],
  );
  return useRemote(`analysis-capabilities:${batch.batch_id}:${batch.snapshot_id}`, load);
}

type ReportView = { type: 'new' } | { type: 'history' } | { type: 'saved'; resultId: string };

export function ReportWorkspace({ batch, contextKey }: { batch: ApiBatch; contextKey: string }) {
  const { client, conditions, reportDraft, activeReport, setActiveReport } = useDataMode();
  const draft = reportDraft?.key === contextKey ? reportDraft : null;
  const [view, setView] = useState<ReportView>(() =>
    activeReport?.batchId === batch.batch_id && activeReport.snapshotId === batch.snapshot_id
      ? { type: 'saved', resultId: activeReport.resultId }
      : draft?.showSaved
        ? { type: 'history' }
        : { type: 'new' },
  );
  const permissions = useReportPermissions(batch);
  const scopeConditions = draft?.spec.conditions ?? conditions;
  const selection = draft?.discovery;
  const load = useCallback(
    async (signal: AbortSignal): Promise<ReportPreview> => {
      const spec: DiscoverySpec = {
        snapshot_id: batch.snapshot_id,
        scope_conditions: scopeConditions,
        profile: 'edu.discovery.profile.v1',
      };
      const result = await client.discoveries(batch, spec, 0, signal, 50);
      if (
        result.items.length !== result.retained_count ||
        (selection && selection.input_key !== result.scope.input_key)
      )
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      const items = result.items.filter(
        (item) =>
          item.status !== 'insufficient_evidence' &&
          (!selection || selection.discovery_ids.includes(item.discovery_id)),
      );
      if (selection && items.length !== selection.discovery_ids.length)
        throw new ApiError(404, 'NOT_FOUND');
      return {
        spec,
        items,
        discovery: {
          input_key: result.scope.input_key,
          discovery_ids: items.map((item) => item.discovery_id),
        },
        matched: result.scope.scope_record_count,
        total: result.scope.batch_record_count,
      };
    },
    [client, batch, scopeConditions, selection],
  );
  const preview = useRemote(view.type === 'new' ? `report-preview:${contextKey}` : null, load);

  const openSaved = (resultId: string) => {
    remember(resultId);
    setView({ type: 'saved', resultId });
  };
  const remember = (resultId: string) =>
    setActiveReport({ batchId: batch.batch_id, snapshotId: batch.snapshot_id, resultId });
  const changeView = (type: 'new' | 'history') => {
    setActiveReport(null);
    setView({ type });
  };
  const history = () => changeView('history');
  const create = () => changeView('new');

  if (view.type === 'history')
    return <SavedAnalyses batch={batch} onOpen={openSaved} onNew={create} />;

  if (view.type === 'saved')
    return (
      <AnalysisDetail
        key={view.resultId}
        batch={batch}
        resultId={view.resultId}
        onBack={history}
        onNew={create}
      />
    );

  return preview.data && permissions.data ? (
    <AnalysisEditor
      batch={batch}
      preview={preview.data}
      permissions={permissions.data}
      onBack={history}
      onSaved={remember}
    />
  ) : (
    <Panel title="分析报告" action={<Button onClick={history}>已保存分析</Button>}>
      {(preview.loading || permissions.loading) && <LoadingPanel />}
      {preview.error && <RequestError error={preview.error} retry={preview.retry} />}
      {permissions.error && <RequestError error={permissions.error} retry={permissions.retry} />}
    </Panel>
  );
}

function SavedAnalyses({
  batch,
  onOpen,
  onNew,
}: {
  batch: ApiBatch;
  onOpen: (resultId: string) => void;
  onNew: () => void;
}) {
  const { client } = useDataMode();
  const [offset, setOffset] = useState(0);
  const loadList = useCallback(
    (signal: AbortSignal) => client.analyses(batch, offset, signal),
    [client, batch, offset],
  );
  const list = useRemote(`saved-list:${batch.batch_id}:${batch.snapshot_id}:${offset}`, loadList);
  return (
    <section className="report-surface" aria-label="已保存分析">
      <Panel
        title="已保存分析"
        action={
          <Button type="primary" onClick={onNew}>
            新建分析报告
          </Button>
        }
      >
        <p className="muted">当前测评：{batch.title}</p>
        {list.loading && <LoadingPanel />}
        {list.error && <RequestError error={list.error} retry={list.retry} />}
        {list.data && (
          <>
            {!list.data.items.length && (
              <Empty description="本次测评还没有报告。选择「新建分析报告」开始整理。" />
            )}
            <div className="saved-analysis-list">
              {list.data.items.map((item) => (
                <Panel
                  key={item.result_id}
                  title={item.title}
                  subtitle={`已保存 · ${new Date(item.updated_at).toLocaleString('zh-CN')}`}
                  action={<Button onClick={() => onOpen(item.result_id)}>查看与核验</Button>}
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
      </Panel>
    </section>
  );
}

function AnalysisDetail({
  batch,
  resultId,
  onBack,
  onNew,
}: {
  batch: ApiBatch;
  resultId: string;
  onBack: () => void;
  onNew: () => void;
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
  return resource.data ? (
    <AnalysisEditor
      batch={batch}
      initial={resource.data.result}
      permissions={resource.data.permissions}
      onBack={onBack}
      onNew={onNew}
    />
  ) : (
    <Panel title="分析报告" action={<Button onClick={onBack}>返回报告列表</Button>}>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
    </Panel>
  );
}

function AnalysisEditor({
  batch,
  preview,
  initial,
  permissions,
  onBack,
  onNew,
  onSaved,
}: {
  batch: ApiBatch;
  permissions: Capabilities;
  onBack: () => void;
  onNew?: () => void;
  onSaved?: (id: string) => void;
} & ({ preview: ReportPreview; initial?: never } | { preview?: never; initial: AnalysisResult })) {
  const { client } = useDataMode();
  const [modal, contextHolder] = Modal.useModal();
  const [result, setResult] = useState<AnalysisResult | undefined>(initial);
  const [title, setTitle] = useState(initial?.title ?? `${batch.title} · 学情分析`.slice(0, 100));
  const [note, setNote] = useState(initial?.note ?? '');
  const editable = allowed(permissions, result ? 'edit_analysis' : 'save_analysis');
  const reportItems = (saved?: AnalysisResult) =>
    saved
      ? [
          ...saved.findings.map(({ candidate, teacher_review }) => ({
            id: candidate.candidate_id,
            title: candidate.title,
            summary: candidate.summary,
            denominator: candidate.denominator,
            limitations: candidate.limitations,
            decision: teacher_review,
            candidate,
            formal: false,
          })),
          ...saved.discovery_findings.map(({ discovery, teacher_review }) => ({
            id: discovery.discovery_id,
            title: discovery.title,
            summary: discovery.summary,
            denominator: discovery.scope.scope_record_count,
            limitations: discovery.limitations,
            decision: teacher_review,
            candidate: null,
            formal: true,
          })),
        ]
      : (preview?.items ?? []).map((item) => ({
          id: item.discovery_id,
          title: item.title,
          summary: item.summary,
          denominator: preview?.matched ?? 0,
          limitations: [],
          decision: { candidate_id: item.discovery_id, status: 'confirmed' as const, note: '' },
          candidate: null,
          formal: true,
        }));
  const defaultDecisions = (saved?: AnalysisResult) =>
    reportItems(saved).map(({ decision }) => ({
      ...decision,
      status: editable && decision.status === 'pending' ? ('confirmed' as const) : decision.status,
    }));
  const items = reportItems(result);
  const [decisions, setDecisions] = useState(() => defaultDecisions(initial));
  const byId = new Map(decisions.map((decision) => [decision.candidate_id, decision]));
  const changes = items.flatMap(({ id, decision }) => {
    const next = byId.get(id);
    return next && (decision.status !== next.status || decision.note !== next.note) ? [next] : [];
  });
  const dirty = !!result && (title !== result.title || note !== result.note || changes.length > 0);
  const unsaved = result
    ? dirty
    : title !== `${batch.title} · 学情分析`.slice(0, 100) || !!note || changes.length > 0;
  const [expanded, setExpanded] = useState(items[0]?.id);
  const [formalDetail, setFormalDetail] = useState<string | null>(null);
  const [selection, setSelection] = useState<MetricSelection | null>(null);
  const [exporting, setExporting] = useState(false);
  const operation = useOperation();
  const conditions = result?.analysis.query.conditions ?? preview?.spec.scope_conditions ?? [];
  const matched = result?.analysis.matched_count ?? preview?.matched ?? 0;
  const total = result?.analysis.total_count ?? preview?.total ?? 0;
  const limitations = [...new Set(items.flatMap((item) => item.limitations))];
  const detailSpec = result?.analysis.discovery?.spec ?? preview?.spec;
  const openNotes = () => document.getElementById('review-analysis-title')?.focus();

  function leave(next: () => void) {
    if (!unsaved) {
      next();
      return;
    }
    modal.confirm({
      title: '存在未保存的修改',
      content: '离开将放弃当前修改。',
      okText: '放弃修改',
      cancelText: '继续编辑',
      onOk: next,
    });
  }

  function update(candidateId: string, patch: Partial<Pick<FindingDecision, 'status' | 'note'>>) {
    setDecisions((previous) =>
      previous.map((decision) =>
        decision.candidate_id === candidateId ? { ...decision, ...patch } : decision,
      ),
    );
  }

  function applySaved(saved: AnalysisResult) {
    setResult(saved);
    setTitle(saved.title);
    setNote(saved.note);
    setDecisions(defaultDecisions(saved));
  }

  function reload() {
    if (!result) return;
    void operation.run(async (signal) => {
      const latest = await client.analysis(batch, result.result_id, signal);
      if (!signal.aborted) applySaved(latest);
    });
  }

  function save() {
    void operation.run(async (signal) => {
      let saved = result;
      if (!saved && preview) {
        saved = await client.createAnalysis(
          batch,
          {
            snapshot_id: batch.snapshot_id,
            conditions: preview.spec.scope_conditions,
            include_dimensions: false,
            discovery: preview.discovery,
            title: title.trim(),
            note,
          },
          signal,
        );
        // Keep the created ID even if the decision write fails. A retry then uses PATCH.
        if (signal.aborted) return;
        setResult(saved);
        onSaved?.(saved.result_id);
      }
      if (!saved) return;
      const writeDecisions = result ? changes : decisions;
      if (result || writeDecisions.length) {
        saved = await client.patchAnalysis(
          batch,
          saved.result_id,
          {
            snapshot_id: saved.snapshot_id,
            expected_version: saved.version,
            title: title.trim(),
            note,
            decisions: writeDecisions,
          },
          signal,
        );
      }
      if (!signal.aborted) applySaved(saved);
    });
  }

  return (
    <section role="region" aria-label="分析核验" className="report-surface report-editor-surface">
      <header className="report-top-toolbar">
        <button
          className="report-title-edit"
          disabled={!editable || operation.busy}
          onClick={openNotes}
        >
          {title}
          <EditOutlined aria-hidden="true" />
        </button>
        <span
          className={`report-save-state${!result || dirty ? ' is-dirty' : ''}`}
          aria-live="polite"
        >
          {result && !dirty && <CheckCircleOutlined aria-hidden="true" />}
          {!result ? '未保存' : dirty ? '未保存修改' : '已保存'}
        </span>
        <Button
          type="link"
          icon={<ArrowLeftOutlined aria-hidden="true" />}
          disabled={operation.busy}
          onClick={() => leave(onBack)}
        >
          {result ? '返回报告列表' : '已保存分析'}
        </Button>
      </header>
      {contextHolder}
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
              <dd>{conditionText(conditions)}</dd>
            </div>
            <div>
              <dt>群体人数</dt>
              <dd>
                {matched} / {total} 人
              </dd>
            </div>
            <div>
              <dt>占比</dt>
              <dd>
                {ratioDisplay(
                  result ? result.analysis.ratio.value : total ? matched / total : null,
                )}
              </dd>
            </div>
          </dl>
          {result && (
            <details className="technical-details">
              <summary className="saved-version">已保存版本 {result.version}</summary>
              <div className="batch-actions">
                <Button disabled={operation.busy} onClick={() => leave(reload)}>
                  加载最新版本
                </Button>
                {onNew && (
                  <Button disabled={operation.busy} onClick={() => leave(onNew)}>
                    新建分析报告
                  </Button>
                )}
              </div>
            </details>
          )}
          {!editable && (
            <Alert
              type="info"
              title={
                result
                  ? '当前为只读权限，可查看核验结果和导出测评报告。'
                  : '当前权限可查看已保存分析。'
              }
              showIcon
            />
          )}
          {items.length > 0 && (
            <div className="review-progress" aria-label="报告发现数量">
              {reportDecisions.map(({ value, label }) => (
                <div key={value} className={`review-progress-count ${value}`}>
                  <span>{label}</span>
                  <strong>
                    {decisions.filter((decision) => decision.status === value).length}
                  </strong>
                </div>
              ))}
            </div>
          )}
          <details className="report-limitations">
            <summary>使用限制</summary>
            <p>{result?.notice ?? '报告仅供授权教师核查。评分关系不能单独用于判断学习成因。'}</p>
            {limitations.length > 0 && (
              <ul>
                {limitations.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            )}
          </details>
          {operation.error && <RequestError error={operation.error} />}
          <div className="report-action-bar">
            {editable && (
              <Button
                loading={operation.busy}
                disabled={(!!result && !dirty) || !title.trim()}
                onClick={save}
              >
                {result ? '保存核验结果' : '保存报告'}
              </Button>
            )}
            <Button
              type="primary"
              disabled={
                !result || dirty || operation.busy || !allowed(permissions, 'export_analysis')
              }
              onClick={() => setExporting(true)}
            >
              导出测评报告
            </Button>
          </div>
        </aside>
        <div className="report-editor-findings">
          <div className="review-section-heading">
            <h2 id="report-findings-heading">发现核验</h2>
          </div>
          <div
            className="report-finding-list"
            role="region"
            aria-labelledby="report-findings-heading"
            tabIndex={0}
          >
            {!items.length && (
              <Empty
                description={
                  result
                    ? '本次分析没有保存候选发现，仍可导出测评报告。'
                    : '当前范围没有符合条件的发现，可导出测评概况。'
                }
              />
            )}
            {items.map((item, index) => {
              const decision = byId.get(item.id) ?? item.decision;
              return (
                <details
                  className="review-finding"
                  key={item.id}
                  data-finding-id={item.id}
                  open={expanded === item.id}
                >
                  <summary
                    className="review-finding-heading"
                    onClick={(event) => {
                      event.preventDefault();
                      setExpanded(expanded === item.id ? undefined : item.id);
                    }}
                  >
                    <span className="review-finding-index">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <h3>{item.title}</h3>
                    <Tag color={decision.status === 'confirmed' ? 'green' : 'default'}>
                      {reportDecisions.find(({ value }) => value === decision.status)?.label ??
                        '未纳入导出'}
                    </Tag>
                    <DownOutlined className="review-chevron" aria-hidden="true" />
                  </summary>
                  <div className="review-finding-content">
                    <p className="muted small">统计范围 {item.denominator} 人</p>
                    <p>{discoverySummaryText(item.summary)}</p>
                    <div className="batch-actions">
                      {item.candidate?.metrics
                        .filter((metric) => metric.key === 'mean' || metric.key === 'matched_count')
                        .map((metric) => (
                          <Button
                            key={metric.key}
                            onClick={() =>
                              result &&
                              setSelection({
                                candidateId: item.id,
                                metricKey: metric.key,
                                title: item.title,
                                spec: result.analysis.query,
                              })
                            }
                          >
                            查看评分依据 · {metricDisplay(metric)}
                          </Button>
                        ))}
                      {item.formal && detailSpec && (
                        <Button onClick={() => setFormalDetail(item.id)}>
                          查看完整发现与评分依据
                        </Button>
                      )}
                    </div>
                    <label className="review-decision-label" id={`decision-${index}`}>
                      报告内容<span className="visually-hidden"> · {item.title}</span>
                    </label>
                    <Radio.Group
                      className="report-decision-options"
                      aria-labelledby={`decision-${index}`}
                      options={reportDecisions.map(({ value, label }) => ({ value, label }))}
                      optionType="button"
                      value={decision.status}
                      disabled={!editable || operation.busy}
                      onChange={(event) => update(item.id, { status: event.target.value })}
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
                      autoSize={{ minRows: 2, maxRows: 5 }}
                      disabled={!editable || operation.busy}
                      onChange={(e) => update(item.id, { note: e.target.value })}
                    />
                  </div>
                </details>
              );
            })}
          </div>
        </div>
      </div>
      {formalDetail && detailSpec && (
        <DiscoveryDetailDrawer
          batch={batch}
          spec={detailSpec}
          discoveryId={formalDetail}
          allowExplore={false}
          onClose={() => setFormalDetail(null)}
        />
      )}
      {exporting && result && (
        <ReportExport
          batch={batch}
          result={result}
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
    </section>
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
