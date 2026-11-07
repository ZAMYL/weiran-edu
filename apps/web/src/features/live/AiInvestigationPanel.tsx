'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Drawer, Input, Pagination, Select, Table, Tag } from 'antd';
import { useRouter } from 'next/navigation';
import { useDataMode } from '../../app/data-mode';
import type { ApiBatch } from '../../services/edu-api/types';
import { ApiError } from '../../services/edu-api/client';
import {
  aiRunning,
  taskStateLabels,
  stopLabels,
  type AiFact,
  type AiTask,
} from '../../services/edu-api/ai-task-types';
import { conditionText, dimensionName } from '../../services/edu-api/view-model';
import { Panel } from '../../components/ui';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import { LiveScoreDrawer } from './LiveStudents';

const metricLabels: Record<string, string> = {
  'metrics.rho': '相关系数',
  'metrics.paired_count': '有效配对人数',
  'metrics.excluded_count': '排除人数',
  'metrics.denominator': '参评人数',
  'metrics.target_count': '目标组人数',
  'metrics.remainder_count': '其余组人数',
  'metrics.target_mean': '目标组均分',
  'metrics.remainder_mean': '其余组均分',
  'metrics.difference': '均分差',
};
const methodLabels: Record<string, string> = {
  query_dimension_relation: '评分关联',
  compare_student_groups: '群体评分差异',
  get_dimension_statistics: '评分统计',
};

export default function AiInvestigationPanel({ batch }: { batch: ApiBatch }) {
  const { client, teacherId, setConditions, setActiveReport, setReportDraft } = useDataMode();
  const router = useRouter();
  const [question, setQuestion] = useState('');
  const [task, setTask] = useState<AiTask | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [sources, setSources] = useState<AiFact | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const pending = useRef<AbortController | null>(null);
  const submission = useRef<{ question: string; key: string } | null>(null);
  const storageKey = `weiran.edu.ai-task.${teacherId}.${batch.batch_id}.${batch.snapshot_id}`;
  useEffect(() => {
    const controller = new AbortController();
    let taskId: string | null = null;
    try {
      taskId = sessionStorage.getItem(storageKey);
    } catch {
      /* optional pointer */
    }
    void (async () => {
      try {
        if (taskId) setTask(await client.aiTask(batch, taskId, controller.signal));
      } catch (e) {
        if (!controller.signal.aborted && e instanceof ApiError) setError(e);
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    })();
    return () => {
      controller.abort();
      pending.current?.abort();
    };
  }, [client, batch, storageKey]);
  useEffect(() => {
    if (!task || !aiRunning(task)) return;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const next = await client.aiTask(batch, task.task_id, controller.signal);
        if (controller.signal.aborted) return;
        setTask(next);
      } catch (e) {
        if (!controller.signal.aborted && e instanceof ApiError) setError(e);
      }
    };
    const timer = setTimeout(poll, 500);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [client, batch, task]);
  async function operate(work: (signal: AbortSignal) => Promise<void>) {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setError(null);
    try {
      await work(controller.signal);
    } catch (e) {
      if (!controller.signal.aborted && e instanceof ApiError) setError(e);
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
      }
    }
  }
  function start() {
    const text = question.trim();
    if (!text || text.length > 300) return;
    if (submission.current?.question !== text)
      submission.current = { question: text, key: crypto.randomUUID() };
    void operate(async (signal) => {
      const value = await client.startAiTask(batch, text, submission.current!.key, signal);
      if (signal.aborted) return;
      setTask(value);
      setSelected([]);
      try {
        sessionStorage.setItem(storageKey, value.task_id);
      } catch {
        /* optional pointer */
      }
    });
  }
  function review() {
    if (!task || !selected.length) return;
    void operate(async (signal) => {
      const receipt = await client.submitAiReview(batch, task.task_id, selected, signal);
      if (signal.aborted) return;
      setReportDraft(null);
      setActiveReport({
        batchId: batch.batch_id,
        snapshotId: batch.snapshot_id,
        resultId: receipt.result_id,
      });
      router.push('/reports');
    });
  }
  const facts =
    task?.result?.facts.filter((fact) => task.result!.selected_fact_ids.includes(fact.fact_id)) ??
    [];
  return (
    <Panel
      title="AI 辅助分析"
      action={<Tag color="cyan">合成 HTTP Mock · Qwen 未调用</Tag>}
      className="ai-investigation-panel"
    >
      <p className="muted small">分析范围：本次测评全部 {batch.student_count} 名授权参评者</p>
      <label htmlFor="ai-question">想了解什么？</label>
      <Input.TextArea
        id="ai-question"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        maxLength={300}
        autoSize={{ minRows: 2, maxRows: 4 }}
        placeholder="有哪些值得关注的问题？"
        disabled={busy || restoring || aiRunning(task)}
      />
      <div className="batch-actions" style={{ marginTop: 12 }}>
        <Button
          type="primary"
          disabled={!question.trim() || busy || restoring || aiRunning(task)}
          loading={busy}
          onClick={start}
        >
          开始分析
        </Button>
        {aiRunning(task) && (
          <Button
            disabled={busy}
            onClick={() =>
              void operate(async (signal) => {
                const value = await client.cancelAiTask(batch, task!.task_id, signal);
                if (!signal.aborted) setTask(value);
              })
            }
          >
            停止分析
          </Button>
        )}
        {task && <span role="status">{taskStateLabels[task.state]}</span>}
      </div>
      {restoring && <p className="muted">正在读取本测评的分析任务…</p>}
      {error && (
        <RequestError
          error={error}
          retry={
            task
              ? () =>
                  void operate(async (signal) => {
                    const value = await client.aiTask(batch, task.task_id, signal);
                    if (!signal.aborted) setTask(value);
                  })
              : undefined
          }
        />
      )}
      {task?.error_code && (
        <Alert
          showIcon
          type="warning"
          title={taskStateLabels[task.state]}
          description={
            <details>
              <summary>技术详情</summary>
              {task.error_code}
            </details>
          }
        />
      )}
      {task?.result && (
        <>
          <p>{stopLabels[task.result.stop_reason] ?? '本轮分析已结束，请查看结果范围。'}</p>
          {task.result.missing_evidence.length > 0 && (
            <Alert
              type="warning"
              showIcon
              title="进一步解释需要补充资料"
              description={task.result.missing_evidence
                .map(
                  (key) =>
                    ({ rubric_criteria: '评分量规', original_task: '原始题目或答卷' })[key] ??
                    '相关依据',
                )
                .join('、')}
            />
          )}
          <div className="ai-fact-grid">
            {facts.map((fact) => (
              <section className="discovery-card" data-testid="ai-fact-card" key={fact.fact_id}>
                <h3>{methodLabels[fact.method] ?? '分析结果'}</h3>
                <p>
                  {[
                    ...new Set(fact.source_refs.map((ref) => dimensionName(ref.dimension_code))),
                  ].join('、')}
                </p>
                {!!fact.conditions.length && <p>{conditionText(fact.conditions)}</p>}
                <dl className="focus-metrics" aria-label="AI 发现指标">
                  {fact.metrics
                    .filter((metric) => metricLabels[metric.key])
                    .map((metric) => (
                      <div key={metric.key}>
                        <dt>{metricLabels[metric.key]}</dt>
                        <dd>
                          <strong>{metric.display}</strong>
                        </dd>
                      </div>
                    ))}
                </dl>
                <div className="ai-fact-footer">
                  <p className="muted">评分现象用于核查，不直接解释学习成因。</p>
                  <div className="ai-fact-actions">
                    {!!fact.source_refs.length && fact.source_verified && (
                      <Button onClick={() => setSources(fact)}>查看依据</Button>
                    )}
                    {fact.exploration_query && fact.source_verified && (
                      <Button
                        onClick={() => {
                          setConditions(fact.exploration_query!.conditions);
                          router.push('/explorer');
                        }}
                      >
                        继续探索
                      </Button>
                    )}
                    {fact.qualified && fact.source_verified && (
                      <Checkbox
                        checked={selected.includes(fact.fact_id)}
                        disabled={!selected.includes(fact.fact_id) && selected.length >= 3}
                        onChange={(e) =>
                          setSelected((ids) =>
                            e.target.checked
                              ? [...ids, fact.fact_id]
                              : ids.filter((id) => id !== fact.fact_id),
                          )
                        }
                      >
                        加入待核查
                      </Checkbox>
                    )}
                  </div>
                </div>
              </section>
            ))}
          </div>
          {!!selected.length && (
            <Button type="primary" loading={busy} onClick={review}>
              提交待核查（{selected.length}）
            </Button>
          )}
        </>
      )}
      {task && sources && (
        <AiSourcesDrawer
          batch={batch}
          task={task}
          fact={sources}
          onClose={() => setSources(null)}
        />
      )}
    </Panel>
  );
}

function AiSourcesDrawer({
  batch,
  task,
  fact,
  onClose,
}: {
  batch: ApiBatch;
  task: AiTask;
  fact: AiFact;
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const [metric, setMetric] = useState(fact.source_refs[0]!.metric_key);
  const [offset, setOffset] = useState(0);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) => client.aiSources(batch, task, fact.fact_id, metric, offset, signal),
    [client, batch, task, fact.fact_id, metric, offset],
  );
  const resource = useRemote(
    `ai-sources:${task.task_id}:${fact.fact_id}:${metric}:${offset}`,
    load,
  );
  return (
    <Drawer title="分析依据" open onClose={onClose} size={620}>
      <Select
        aria-label="选择依据指标"
        style={{ width: '100%' }}
        value={metric}
        onChange={(value) => {
          setMetric(value);
          setOffset(0);
        }}
        options={fact.source_refs.map((ref, index) => ({
          value: ref.metric_key,
          label: `${index + 1} · ${dimensionName(ref.dimension_code)} · ${ref.participant_count} 人`,
        }))}
      />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data && (
        <>
          <Table
            rowKey="evidence_id"
            dataSource={resource.data.items}
            size="small"
            pagination={false}
            columns={[
              { title: '记录', render: (_, row, index) => offset + index + 1 },
              { title: '能力', render: (_, row) => dimensionName(row.dimension_code) },
              {
                title: '操作',
                render: (_, row) => (
                  <Button onClick={() => setEvidenceId(row.evidence_id)}>查看原评分依据</Button>
                ),
              },
            ]}
          />
          <Pagination
            current={offset / 10 + 1}
            pageSize={10}
            showSizeChanger={false}
            total={resource.data.total_count}
            onChange={(page) => setOffset((page - 1) * 10)}
          />
        </>
      )}
      {evidenceId && (
        <LiveScoreDrawer
          batch={batch}
          evidenceId={evidenceId}
          onClose={() => setEvidenceId(null)}
        />
      )}
    </Drawer>
  );
}
