'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  Modal,
  Pagination,
  Segmented,
  Tag,
} from 'antd';
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
} from '../../services/edu-api/analysis-types';
import { saveReportFile } from '../../services/edu-api/report-file';
import type { ApiBatch, InsightSpec } from '../../services/edu-api/types';
import { conditionText, metricDisplay } from '../../services/edu-api/view-model';
import { MetricEvidenceDrawer, type MetricSelection } from './LiveStudents';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';

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
}: {
  batch: ApiBatch;
  spec: InsightSpec;
  blocked?: string;
}) {
  const { client } = useDataMode();
  const [save, setSave] = useState(false);
  const [history, setHistory] = useState<{ resultId: string | null } | null>(null);
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
      <section className="panel analysis-actions">
        <div>
          <h2>保存与核验</h2>
          <p className="muted">保存当前已应用条件，核验系统候选后导出测评报告。</p>
        </div>
        <div className="batch-actions">
          <Button
            type="primary"
            disabled={Boolean(blocked) || !allowed(permissions.data, 'save_analysis')}
            onClick={() => setSave(true)}
          >
            保存本次分析
          </Button>
          <Button onClick={() => setHistory({ resultId: null })}>已保存分析</Button>
        </div>
        {blocked && <p className="muted small">{blocked}</p>}
        {permissions.error && <RequestError error={permissions.error} retry={permissions.retry} />}
        {permissions.data && !allowed(permissions.data, 'save_analysis') && (
          <p className="muted small">当前权限可查看已保存分析；保存和核验需批次所有者权限。</p>
        )}
      </section>
      {save && (
        <SaveAnalysis
          batch={batch}
          spec={spec}
          onClose={() => setSave(false)}
          onSaved={(result) => {
            setSave(false);
            setHistory({ resultId: result.result_id });
          }}
        />
      )}
      {history && (
        <SavedAnalyses
          batch={batch}
          initialResultId={history.resultId}
          onClose={() => setHistory(null)}
        />
      )}
    </>
  );
}

function SaveAnalysis({
  batch,
  spec,
  onClose,
  onSaved,
}: {
  batch: ApiBatch;
  spec: InsightSpec;
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
            { ...spec, title: title.trim(), note },
            signal,
          );
          if (!signal.aborted) onSaved(result);
        })
      }
    >
      <p>分析范围：{conditionText(spec.conditions)}</p>
      <p className="muted small">
        {spec.include_dimensions ? '包含五维评分候选' : '不包含维度候选'}
        ；完整统计和候选由服务端重新计算并保存，不受当前分页影响。
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
  initialResultId,
  onClose,
}: {
  batch: ApiBatch;
  initialResultId: string | null;
  onClose: () => void;
}) {
  const { client } = useDataMode();
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
        batch={batch}
        resultId={resultId}
        onClose={onClose}
        onBack={() => {
          setResultId(null);
          list.retry();
        }}
      />
    );
  return (
    <Drawer title="已保存分析" open onClose={onClose} size="min(760px, 100vw)">
      <p className="muted">当前批次：{batch.title} · 当前快照</p>
      <Button onClick={list.retry}>刷新分析列表</Button>
      {list.loading && <LoadingPanel />}
      {list.error && <RequestError error={list.error} retry={list.retry} />}
      {list.data && (
        <>
          {!list.data.items.length && <Empty description="当前快照还没有保存的分析。" />}
          <div className="saved-analysis-list">
            {list.data.items.map((item) => (
              <Panel
                key={item.result_id}
                title={item.title}
                subtitle={`版本 ${item.version} · ${item.updated_at}`}
                action={<Button onClick={() => setResultId(item.result_id)}>查看与核验</Button>}
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
    </Drawer>
  );
}

function AnalysisDetail({
  batch,
  resultId,
  onClose,
  onBack,
}: {
  batch: ApiBatch;
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
    <Drawer title="已保存分析" open onClose={onClose} size="min(900px, 100vw)">
      <Button onClick={onBack}>返回分析列表</Button>
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
    </Drawer>
  );
}

function AnalysisEditor({
  batch,
  initial,
  editable,
  reload,
  onClose,
  onBack,
}: {
  batch: ApiBatch;
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
  const [decisions, setDecisions] = useState<FindingDecision[]>(() =>
    initial.findings.map((finding) => ({ ...finding.teacher_review })),
  );
  const [exporting, setExporting] = useState(false);
  const [selection, setSelection] = useState<MetricSelection | null>(null);
  const operation = useOperation();
  const changes = decisions.filter((decision) => {
    const previous = initial.findings.find(
      (finding) => finding.candidate.candidate_id === decision.candidate_id,
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
    <Drawer
      title="分析核验"
      open
      onClose={() => leave(onClose)}
      maskClosable={!operation.busy}
      closable={!operation.busy}
      keyboard={!operation.busy}
      size="min(900px, 100vw)"
    >
      {contextHolder}
      <div className="batch-actions">
        <Button disabled={operation.busy} onClick={() => leave(onBack)}>
          返回分析列表
        </Button>
        <Button disabled={operation.busy} onClick={() => leave(reload)}>
          加载最新版本
        </Button>
        <Button
          type="primary"
          disabled={dirty || operation.busy}
          onClick={() => setExporting(true)}
        >
          导出测评报告
        </Button>
      </div>
      <p className="saved-version">
        已保存版本 {initial.version} · 快照 <code>{initial.snapshot_id}</code>
      </p>
      <p>保存的分析范围：{conditionText(initial.analysis.query.conditions)}</p>
      <p>
        群体人数 {initial.analysis.matched_count} / {initial.analysis.total_count} ·{' '}
        {initial.analysis.ratio.display}
      </p>
      <p className="muted small">{initial.notice}</p>
      {!editable && (
        <Alert type="info" title="当前为只读权限，可查看核验结果和导出测评报告。" showIcon />
      )}
      <div className="analysis-fields">
        <label htmlFor="review-analysis-title">已保存分析名称</label>
        <Input
          id="review-analysis-title"
          value={title}
          maxLength={100}
          disabled={!editable || operation.busy}
          onChange={(e) => setTitle(e.target.value)}
        />
        <label htmlFor="review-analysis-note">分析备注</label>
        <Input.TextArea
          id="review-analysis-note"
          value={note}
          maxLength={2000}
          disabled={!editable || operation.busy}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      <h2 className="review-heading">候选发现核验</h2>
      {!initial.findings.length && (
        <Empty description="本次分析没有保存候选发现，仍可导出测评报告。" />
      )}
      {initial.findings.map(({ candidate }, index) => {
        const decision = decisions[index]!;
        return (
          <section className="panel review-finding" key={candidate.candidate_id}>
            <div className="finding-top">
              <Tag
                color={
                  decision.status === 'confirmed'
                    ? 'green'
                    : decision.status === 'dismissed'
                      ? 'default'
                      : 'gold'
                }
              >
                教师{decisionLabels[decision.status]}
              </Tag>
              <small>统计范围 {candidate.denominator} 人</small>
            </div>
            <h3>{candidate.title}</h3>
            <p>{candidate.summary}</p>
            <div className="batch-actions">
              {candidate.metrics
                .filter((metric) => metric.key === 'mean' || metric.key === 'matched_count')
                .map((metric) => (
                  <Button
                    key={metric.key}
                    onClick={() =>
                      setSelection({
                        candidateId: candidate.candidate_id,
                        metricKey: metric.key,
                        title: candidate.title,
                        spec: initial.analysis.query,
                      })
                    }
                  >
                    查看评分证据 · {metricDisplay(metric)}
                  </Button>
                ))}
            </div>
            <label className="review-decision-label" id={`decision-${index}`}>
              教师判断 · {candidate.title}
            </label>
            <Segmented
              aria-labelledby={`decision-${index}`}
              options={Object.entries(decisionLabels).map(([value, label]) => ({ value, label }))}
              value={decision.status}
              disabled={!editable || operation.busy}
              onChange={(value) =>
                update(candidate.candidate_id, { status: value as DecisionStatus })
              }
            />
            <label htmlFor={`finding-note-${index}`}>核验备注 · {candidate.title}</label>
            <Input.TextArea
              id={`finding-note-${index}`}
              value={decision.note}
              maxLength={2000}
              disabled={!editable || operation.busy}
              onChange={(e) => update(candidate.candidate_id, { note: e.target.value })}
            />
            <details>
              <summary>查看候选限制</summary>
              {candidate.limitations.map((text) => (
                <p className="muted small" key={text}>
                  {text}
                </p>
              ))}
            </details>
          </section>
        );
      })}
      {operation.error && <RequestError error={operation.error} />}
      {dirty && (
        <Alert type="info" showIcon title="修改尚未保存，请先保存核验结果，再导出测评报告。" />
      )}
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
    </Drawer>
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
