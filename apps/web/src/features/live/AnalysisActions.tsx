'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Divider,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Radio,
  Row,
  Segmented,
  Skeleton,
  Space,
  Tag,
  Table,
  Typography,
} from 'antd';
import { ArrowLeftOutlined, CheckCircleOutlined, EditOutlined } from '@ant-design/icons';
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
import { saveReportFile, xlsxDownloadName } from '../../services/edu-api/report-file';
import type { ApiBatch, InsightSpec } from '../../services/edu-api/types';
import { conditionText, metricDisplay, ratioDisplay } from '../../services/edu-api/view-model';
import { MetricEvidenceDrawer, type MetricSelection } from './LiveStudents';
import { RequestError } from './LiveStatus';
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
    <Panel className="analysis-actions" title="分析报告">
      <Flex vertical gap={tokens.sizeXS}>
        <Space wrap size="small" className="batch-actions">
          <Button
            type="primary"
            disabled={Boolean(blocked) || !allowed(permissions.data, 'save_analysis')}
            onClick={() => prepare()}
          >
            准备分析报告
          </Button>
          <Button onClick={() => prepare(true)}>已保存分析</Button>
        </Space>
        {blocked && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {blocked}
          </Typography.Text>
        )}
        {permissions.error && <RequestError error={permissions.error} retry={permissions.retry} />}
        {permissions.data && !allowed(permissions.data, 'save_analysis') && (
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            当前权限可查看已保存分析；保存和核验需批次所有者权限。
          </Typography.Text>
        )}
      </Flex>
    </Panel>
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

type ReportView = { type: 'recent' | 'new' | 'history' } | { type: 'saved'; resultId: string };

function ReportSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <Row
      gutter={[tokens.size, tokens.size]}
      className="report-loading-layout"
      role="status"
      aria-label="正在加载分析报告"
    >
      <Col xs={24} md={7}>
        <Card size="small">
          <Skeleton active title paragraph={{ rows: 4 }} />
        </Card>
      </Col>
      <Col xs={24} md={17}>
        <Card size="small">
          <Flex vertical gap={tokens.size}>
            {Array.from({ length: rows }, (_, index) => (
              <Skeleton key={index} active title paragraph={{ rows: 2 }} />
            ))}
          </Flex>
        </Card>
      </Col>
    </Row>
  );
}

export function ReportWorkspace({ batch, contextKey }: { batch: ApiBatch; contextKey: string }) {
  const { client, conditions, reportDraft, activeReport, setActiveReport } = useDataMode();
  const draft = reportDraft?.key === contextKey ? reportDraft : null;
  const [view, setView] = useState<ReportView>(() =>
    activeReport?.batchId === batch.batch_id && activeReport.snapshotId === batch.snapshot_id
      ? { type: 'saved', resultId: activeReport.resultId }
      : draft?.showSaved
        ? { type: 'history' }
        : { type: draft ? 'new' : 'recent' },
  );
  const overviewKey = `report-list-count:${batch.batch_id}:${batch.snapshot_id}`;
  const loadRecent = useCallback(
    async (signal: AbortSignal) => {
      const list = await client.analyses(batch, 0, signal);
      if (!signal.aborted) client.overviewCache.write(overviewKey, list.total_count);
      return list.items[0]?.result_id ?? '';
    },
    [client, batch, overviewKey],
  );
  const recent = useRemote(view.type === 'recent' ? overviewKey : null, loadRecent);
  const savedId = view.type === 'saved' ? view.resultId : recent.data;
  const needsPreview = view.type === 'new' || (view.type === 'recent' && recent.data === '');
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
  const preview = useRemote(needsPreview ? `report-preview:${contextKey}` : null, load);

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

  if (savedId)
    return (
      <AnalysisDetail
        key={savedId}
        batch={batch}
        resultId={savedId}
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
      {(recent.loading || preview.loading || permissions.loading) && (
        <ReportSkeleton
          rows={Math.min(3, Math.max(1, client.overviewCache.read<number>(overviewKey) ?? 3))}
        />
      )}
      {recent.error && <RequestError error={recent.error} retry={recent.retry} />}
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
        subtitle={`当前测评：${batch.title}`}
        action={
          <Button type="primary" onClick={onNew}>
            新建分析报告
          </Button>
        }
      >
        {list.loading && <ReportSkeleton />}
        {list.error && <RequestError error={list.error} retry={list.retry} />}
        {list.data && (
          <Table
            className="saved-analysis-list"
            rowKey="result_id"
            dataSource={list.data.items}
            locale={{
              emptyText: (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次测评还没有报告" />
              ),
            }}
            pagination={{
              current: Math.floor(offset / 10) + 1,
              total: list.data.total_count,
              pageSize: 10,
              showSizeChanger: false,
              hideOnSinglePage: true,
              onChange: (page) => setOffset((page - 1) * 10),
              showTotal: (total) => `共 ${total} 次分析`,
            }}
            columns={[
              {
                title: '报告名称',
                render: (_, item) => (
                  <Flex vertical gap={tokens.marginXXS}>
                    <Typography.Text strong>{item.title}</Typography.Text>
                    <Typography.Text type="secondary">
                      {new Date(item.updated_at).toLocaleString('zh-CN')}
                    </Typography.Text>
                  </Flex>
                ),
              },
              {
                title: '操作',
                render: (_, item) => (
                  <Button type="link" size="small" onClick={() => onOpen(item.result_id)}>
                    查看与核验
                  </Button>
                ),
              },
            ]}
          />
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
      {resource.loading && <ReportSkeleton />}
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
      <Card size="small" className="report-top-toolbar">
        <Flex align="center" justify="space-between" wrap gap={tokens.sizeSM}>
          <Space size="small" wrap>
            <Button
              type="text"
              className="report-title-edit"
              disabled={!editable || operation.busy}
              onClick={openNotes}
            >
              <Typography.Text strong style={{ fontSize: 15 }}>
                {title}
              </Typography.Text>
              <EditOutlined aria-hidden="true" />
            </Button>
            <Tag
              color={!result || dirty ? 'warning' : 'success'}
              icon={result && !dirty ? <CheckCircleOutlined aria-hidden="true" /> : undefined}
              className={`report-save-state${!result || dirty ? ' is-dirty' : ''}`}
              aria-live="polite"
            >
              {!result ? '未保存' : dirty ? '未保存修改' : '已保存'}
            </Tag>
          </Space>
          <Button
            type="link"
            icon={<ArrowLeftOutlined aria-hidden="true" />}
            disabled={operation.busy}
            onClick={() => leave(onBack)}
          >
            {result ? '返回报告列表' : '已保存分析'}
          </Button>
        </Flex>
      </Card>
      {contextHolder}
      <div className="report-editor-grid">
        <Card
          size="small"
          title="报告信息"
          className="report-editor-info"
          role="complementary"
          aria-label="报告信息"
        >
          <Flex vertical gap={tokens.sizeSM} style={{ height: '100%' }}>
            <Form layout="vertical" component="div" className="analysis-fields">
              <Form.Item
                label="报告名称"
                htmlFor="review-analysis-title"
                style={{ marginBottom: tokens.sizeSM }}
              >
                <Input
                  id="review-analysis-title"
                  value={title}
                  maxLength={100}
                  disabled={!editable || operation.busy}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Form.Item>
              <Form.Item
                label="备注（可选）"
                htmlFor="review-analysis-note"
                style={{ marginBottom: 0 }}
              >
                <Input.TextArea
                  id="review-analysis-note"
                  aria-label="分析备注"
                  value={note}
                  autoSize={{ minRows: 2, maxRows: 5 }}
                  maxLength={2000}
                  showCount
                  disabled={!editable || operation.busy}
                  onChange={(e) => setNote(e.target.value)}
                />
              </Form.Item>
            </Form>
            <Divider style={{ marginBlock: tokens.sizeXXS }} />
            <Descriptions
              size="small"
              column={1}
              className="report-metadata"
              items={[
                { key: 'batch', label: '测评', children: batch.title },
                { key: 'scope', label: '分析范围', children: conditionText(conditions) },
                { key: 'count', label: '群体人数', children: `${matched} / ${total} 人` },
                {
                  key: 'ratio',
                  label: '占比',
                  children: ratioDisplay(
                    result ? result.analysis.ratio.value : total ? matched / total : null,
                  ),
                },
              ]}
            />
            {result && (
              <Disclosure
                className="technical-details"
                headingClassName="saved-version"
                label={<>已保存版本 {result.version}</>}
              >
                <Space wrap size="small" className="batch-actions">
                  <Button disabled={operation.busy} onClick={() => leave(reload)}>
                    加载最新版本
                  </Button>
                  {onNew && (
                    <Button disabled={operation.busy} onClick={() => leave(onNew)}>
                      新建分析报告
                    </Button>
                  )}
                </Space>
              </Disclosure>
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
              <Flex
                vertical
                gap={tokens.sizeXS}
                className="review-progress"
                aria-label="报告发现数量"
              >
                {reportDecisions.map(({ value, label }) => (
                  <Flex
                    key={value}
                    justify="space-between"
                    align="center"
                    className={`review-progress-count ${value}`}
                  >
                    <Badge status={value === 'confirmed' ? 'processing' : 'default'} text={label} />
                    <Typography.Text
                      strong
                      style={
                        value === 'confirmed' ? { color: 'var(--ant-color-primary)' } : undefined
                      }
                    >
                      {decisions.filter((decision) => decision.status === value).length}
                    </Typography.Text>
                  </Flex>
                ))}
              </Flex>
            )}
            <Disclosure className="report-limitations" label={<>使用限制</>}>
              <Typography.Text type="secondary" style={{ fontSize: 13 }}>
                {result?.notice ?? '报告仅供授权教师核查。评分关系不能单独用于判断学习成因。'}
              </Typography.Text>
              {limitations.length > 0 && (
                <ul style={{ marginTop: tokens.sizeXS, paddingInlineStart: tokens.paddingMD }}>
                  {limitations.map((text) => (
                    <li key={text}>{text}</li>
                  ))}
                </ul>
              )}
            </Disclosure>
            {operation.error && <RequestError error={operation.error} />}
            <Flex vertical gap={tokens.sizeXS} className="report-action-bar">
              {editable && (
                <Button
                  block
                  loading={operation.busy}
                  disabled={(!!result && !dirty) || !title.trim()}
                  onClick={save}
                >
                  {result ? '保存核验结果' : '保存报告'}
                </Button>
              )}
              <Button
                block
                type="primary"
                disabled={
                  !result || dirty || operation.busy || !allowed(permissions, 'export_analysis')
                }
                onClick={() => setExporting(true)}
              >
                导出测评报告
              </Button>
            </Flex>
          </Flex>
        </Card>
        <Card
          size="small"
          title={
            <Typography.Title level={4} id="report-findings-heading" style={{ margin: 0 }}>
              发现核验
            </Typography.Title>
          }
          className="report-editor-findings"
        >
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
            <Flex vertical gap={tokens.sizeXS}>
              {items.map((item, index) => {
                const decision = byId.get(item.id) ?? item.decision;
                return (
                  <Disclosure
                    className="review-finding"
                    variant="outlined"
                    key={item.id}
                    data-finding-id={item.id}
                    headingClassName="review-finding-heading"
                    expanded={expanded === item.id}
                    onExpandedChange={(open) => setExpanded(open ? item.id : undefined)}
                    label={
                      <Flex align="center" gap={tokens.sizeSM} style={{ width: '100%' }}>
                        <Typography.Text type="secondary" strong className="review-finding-index">
                          {String(index + 1).padStart(2, '0')}
                        </Typography.Text>
                        <Typography.Text strong style={{ flex: 1, minWidth: 0 }}>
                          {item.title}
                        </Typography.Text>
                        <Tag color={decision.status === 'confirmed' ? 'success' : 'default'}>
                          {reportDecisions.find(({ value }) => value === decision.status)?.label ??
                            '未纳入导出'}
                        </Tag>
                      </Flex>
                    }
                  >
                    <Flex vertical gap={tokens.sizeSM} className="review-finding-content">
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        统计范围 {item.denominator} 人
                      </Typography.Text>
                      <Typography.Paragraph style={{ margin: 0 }}>
                        {discoverySummaryText(item.summary)}
                      </Typography.Paragraph>
                      <Space wrap size="small" className="batch-actions">
                        {item.candidate?.metrics
                          .filter(
                            (metric) => metric.key === 'mean' || metric.key === 'matched_count',
                          )
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
                      </Space>
                      <div>
                        <Typography.Text
                          type="secondary"
                          id={`decision-${index}`}
                          style={{ display: 'block', marginBottom: tokens.marginXS, fontSize: 13 }}
                        >
                          报告内容<span className="visually-hidden"> · {item.title}</span>
                        </Typography.Text>
                        <Radio.Group
                          block
                          className="report-decision-options"
                          aria-labelledby={`decision-${index}`}
                          options={reportDecisions.map(({ value, label }) => ({ value, label }))}
                          optionType="button"
                          value={decision.status}
                          disabled={!editable || operation.busy}
                          onChange={(event) => update(item.id, { status: event.target.value })}
                        />
                      </div>
                      <div>
                        <label
                          htmlFor={`finding-note-${index}`}
                          style={{
                            display: 'block',
                            marginBottom: tokens.marginXS,
                            fontSize: 13,
                            color: 'var(--muted)',
                          }}
                        >
                          核验备注<span className="visually-hidden"> · {item.title}</span>
                        </label>
                        <Input.TextArea
                          id={`finding-note-${index}`}
                          value={decision.note}
                          maxLength={2000}
                          showCount
                          autoSize={{ minRows: 2, maxRows: 5 }}
                          disabled={!editable || operation.busy}
                          onChange={(e) => update(item.id, { note: e.target.value })}
                        />
                      </div>
                    </Flex>
                  </Disclosure>
                );
              })}
            </Flex>
          </div>
        </Card>
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
      <ReportDownload
        key={format}
        batch={batch}
        result={result}
        format={format}
        onFormatChange={setFormat}
        onReload={onReload}
      />
    </Modal>
  );
}

function ReportDownload({
  batch,
  result,
  format,
  onFormatChange,
  onReload,
}: {
  batch: ApiBatch;
  result: AnalysisResult;
  format: ReportFormat;
  onFormatChange: (format: ReportFormat) => void;
  onReload: () => void;
}) {
  const { client } = useDataMode();
  const [acknowledged, setAcknowledged] = useState(false);
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
      <div className="report-download-heading">
        <strong>{result.title}</strong>
        {check.data && (
          <span className="muted">已确认发现 {check.data.confirmed_finding_count} 条</span>
        )}
      </div>
      <Segmented
        block
        aria-label="报告格式"
        options={[
          { label: 'Excel', value: 'xlsx' },
          { label: 'Markdown', value: 'md' },
        ]}
        value={format}
        disabled={operation.busy}
        onChange={onFormatChange}
      />
      {check.error && <RequestError error={check.error} retry={check.retry} />}
      {!!check.data?.warnings.length && (
        <>
          <Alert
            type="warning"
            showIcon
            title={
              check.data.warnings
                .map(
                  ({ code, count }) =>
                    ({
                      PENDING_FINDINGS_EXCLUDED: `${count} 条待核验发现`,
                      DISMISSED_FINDINGS_EXCLUDED: `${count} 条已排除发现`,
                      WITHHELD_COMMENTS_EXCLUDED: `${count} 条未授权评语`,
                      WITHHELD_RUBRIC_EXCLUDED: `${count} 项未授权评分依据`,
                    })[code],
                )
                .join('、') + '不会导出'
            }
          />
          <Checkbox checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)}>
            确认并继续导出
          </Checkbox>
        </>
      )}
      {operation.error && <RequestError error={operation.error} />}
      {blocked && <Button onClick={onReload}>关闭并加载最新结果</Button>}
      <Button
        type="primary"
        loading={operation.busy || check.loading}
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
              const downloadName =
                format === 'xlsx' ? xlsxDownloadName(result.title, result.version) : file.filename;
              saveReportFile(file, downloadName);
            }
          })
        }
      >
        下载 {format === 'xlsx' ? 'Excel' : 'Markdown'}
      </Button>
    </div>
  );
}
