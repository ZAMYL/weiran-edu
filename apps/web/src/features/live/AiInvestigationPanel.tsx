'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Input, Pagination, Segmented, Select, Table } from 'antd';
import { useRouter } from 'next/navigation';
import { useDataMode } from '../../app/data-mode';
import type { ApiBatch, ApiCondition } from '../../services/edu-api/types';
import { ApiError } from '../../services/edu-api/client';
import {
  aiRunning,
  taskStateLabels,
  stopLabels,
  type AiFact,
  type AiProfile,
  type AiProfileDimension,
  type AiTask,
} from '../../services/edu-api/ai-task-types';
import { exactToNumber } from '../../services/edu-api/exact';
import { conditionText, dimensionName, scoreDisplay } from '../../services/edu-api/view-model';
import { Panel } from '../../components/ui';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';
import { LiveScoreDrawer } from './LiveStudents';
import TeacherQuestionSuggestions from './TeacherQuestionSuggestions';
import { teacherAiError, teacherQuestions } from '../../services/edu-api/teacher-ai-guidance';
import { teacherNumber } from '../../services/edu-api/teacher-analysis';
import { DIMENSIONS } from '../../services/edu-api/types';

const legacyItems = DIMENSIONS.map((d) => ({
  item_id: d.code,
  display_name: d.name,
  kind: 'competency' as const,
  scale: null,
}));

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
const profileOperators: Record<string, ApiCondition['operator']> = {
  '<': 'lt',
  '<=': 'lte',
  '==': 'eq',
  '>=': 'gte',
  '>': 'gt',
};
const profileConditions = (profile: AiProfile): ApiCondition[] =>
  profile.exploration_query.conditions.map((condition) => ({
    dimension_code: condition.dimension_code,
    operator: profileOperators[condition.operator]!,
    value: Number(condition.value),
  }));
const exactScore = teacherNumber;

const factMetricValue = (metric: AiFact['metrics'][number]) =>
  metric.exact
    ? exactScore(metric.exact)
    : metric.number != null
      ? scoreDisplay(metric.number)
      : metric.display;

function factTitle(fact: AiFact) {
  const names = [...new Set(fact.source_refs.map((ref) => dimensionName(ref.dimension_code)))];
  return [names.join('、'), methodLabels[fact.method] ?? '评分发现'].filter(Boolean).join(' · ');
}

function factConclusion(fact: AiFact) {
  const difference = fact.metrics.find((metric) => metric.key === 'metrics.difference');
  const relation = fact.metrics.find((metric) => metric.key === 'metrics.rho');
  const metric = difference ?? relation;
  const value = metric?.exact ? exactToNumber(metric.exact) : metric?.number;
  if (value == null) return null;
  if (difference)
    return value === 0 ? '两组评分均分相同' : value > 0 ? '本组均分更高' : '对照组均分更高';
  return value === 0 ? null : value > 0 ? '两项评分整体同向' : '两项评分整体反向';
}
function primaryMetrics(fact: AiFact) {
  const keys =
    fact.method === 'query_dimension_relation'
      ? ['metrics.rho', 'metrics.paired_count']
      : fact.method === 'compare_student_groups'
        ? ['metrics.difference', 'metrics.target_mean', 'metrics.remainder_mean']
        : ['metrics.denominator'];
  return keys.flatMap((key) => fact.metrics.filter((metric) => metric.key === key));
}

export default function AiInvestigationPanel({ batch }: { batch: ApiBatch }) {
  const {
    client,
    teacherId,
    conditions,
    syntheticAi,
    setConditions,
    setActiveReport,
    setReportDraft,
  } = useDataMode();
  const router = useRouter();
  const questions = teacherQuestions(batch.recognized_items ?? legacyItems);
  const [question, setQuestion] = useState(() => questions[0]?.question ?? '');
  const [task, setTask] = useState<AiTask | null>(null);
  const [sources, setSources] = useState<AiFact | null>(null);
  const [profileEvidence, setProfileEvidence] = useState<{
    profile: AiProfile;
    dimension: AiProfileDimension;
  } | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [restoreNotice, setRestoreNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const pending = useRef<AbortController | null>(null);
  const submission = useRef<{ question: string; key: string } | null>(null);
  const storageKey = `weiran.edu.ai-task.${teacherId}.${batch.batch_id}.${batch.snapshot_id}`;
  const running = aiRunning(task);
  const execution = syntheticAi ? 'http_mock' : 'qwen_live';
  const campaignUsed = error?.status === 409 && error.code === 'AI_CAMPAIGN_ALREADY_USED';
  const canStart = !task && !campaignUsed;
  const checkExecution = useCallback(
    (value: AiTask) => {
      if (
        value.execution_origin !== execution &&
        !(execution === 'qwen_live' && value.execution_origin === 'not_run')
      )
        throw new ApiError(502, 'AI_EXECUTION_SOURCE_MISMATCH');
      return value;
    },
    [execution],
  );
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
        if (taskId) setTask(checkExecution(await client.aiTask(batch, taskId, controller.signal)));
      } catch (e) {
        if (!controller.signal.aborted && e instanceof ApiError) {
          // A missing or expired task pointer is not an actionable error: clear it and
          // show a short neutral notice instead of a stale import/expiry error message.
          // Auth/session/source failures (401/403/409/5xx) still surface as errors.
          if (e.status === 404 || e.status === 410) {
            try {
              sessionStorage.removeItem(storageKey);
            } catch {
              /* optional pointer */
            }
            setRestoreNotice('暂时无法读取上次分析任务。');
          } else {
            setError(e);
          }
        }
      } finally {
        if (!controller.signal.aborted) setRestoring(false);
      }
    })();
    return () => {
      controller.abort();
      pending.current?.abort();
    };
  }, [client, batch, storageKey, checkExecution]);
  useEffect(() => {
    if (!task || !aiRunning(task)) return;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const next = checkExecution(await client.aiTask(batch, task.task_id, controller.signal));
        if (controller.signal.aborted) return;
        setTask((current) => (current === task ? next : current));
      } catch (e) {
        if (!controller.signal.aborted && e instanceof ApiError) setError(e);
      }
    };
    const timer = setTimeout(poll, 500);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [client, batch, task, checkExecution]);
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
    if (!canStart || restoring || !text || text.length > 300) return;
    if (submission.current?.question !== text)
      submission.current = { question: text, key: crypto.randomUUID() };
    void operate(async (signal) => {
      const value = checkExecution(
        await client
          .startAiTask(batch, text, submission.current!.key, signal)
          .catch((failure: unknown) => {
            if (
              execution === 'qwen_live' &&
              failure instanceof ApiError &&
              [404, 503].includes(failure.status)
            )
              throw new ApiError(failure.status, 'AI_NOT_AVAILABLE', failure.requestId);
            throw failure;
          }),
      );
      if (signal.aborted) return;
      setTask(value);
      try {
        sessionStorage.setItem(storageKey, value.task_id);
      } catch {
        /* optional pointer */
      }
    });
  }
  function review() {
    if (!task || !reportFacts.length) return;
    void operate(async (signal) => {
      const prefix = `/api/v1/edu/batches/${batch.batch_id}/analysis-results/`;
      const existing = task.review_result_path?.startsWith(prefix)
        ? task.review_result_path.slice(prefix.length)
        : '';
      const resultId = /^[a-zA-Z0-9_-]{1,128}$/.test(existing)
        ? existing
        : (await client.submitAiReview(batch, task.task_id, reportFacts, signal)).result_id;
      if (signal.aborted) return;
      setTask({ ...task, review_result_path: prefix + resultId });
      setReportDraft(null);
      setActiveReport({
        batchId: batch.batch_id,
        snapshotId: batch.snapshot_id,
        resultId,
      });
      router.push('/reports');
    });
  }
  const result = task?.result;
  const factById = new Map(result?.facts.map((fact) => [fact.fact_id, fact]));
  const facts = (result?.selected_fact_ids ?? []).flatMap((id) => {
    const fact = factById.get(id);
    return fact ? [fact] : [];
  });
  const reportFacts = facts
    .filter((fact) => fact.qualified && fact.source_verified)
    .map((fact) => fact.fact_id);
  const projection = task?.result?.profile_projection;
  const profiles = projection?.profiles ?? [];
  const taskLabel = task ? taskStateLabels[task.state] : undefined;
  const taskFailure = task?.error_code ? teacherAiError(task.error_code) : null;
  const resultLabel = task?.result && stopLabels[task.result.stop_reason];
  const analyzing = !error && (running || (canStart && busy));
  const status = error
    ? undefined
    : analyzing
      ? '正在分析'
      : restoring
        ? '正在读取分析任务…'
        : (resultLabel ?? taskLabel);
  const renderFact = (fact: AiFact) => (
    <FactCard
      key={fact.fact_id}
      fact={fact}
      onEvidence={() => setSources(fact)}
      onExplore={() => {
        if (!fact.exploration_query) return;
        setConditions(fact.exploration_query.conditions);
        router.push('/explorer');
      }}
    />
  );
  return (
    <Panel
      title="AI 分析"
      className="ai-investigation-panel"
      subtitle={
        conditions.length > 0 ? `分析范围：全部 ${batch.student_count} 名参评者` : undefined
      }
    >
      {canStart && !restoring && (
        <form
          className="ai-question-form"
          onSubmit={(event) => {
            event.preventDefault();
            start();
          }}
        >
          <TeacherQuestionSuggestions
            questions={questions}
            disabled={busy}
            selected={question}
            onChoose={setQuestion}
          />
          <div className="ai-question-compose">
            <label className="visually-hidden" htmlFor="ai-question">
              想了解什么？
            </label>
            <Input.TextArea
              id="ai-question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              maxLength={300}
              autoSize={{ minRows: 1, maxRows: 4 }}
              placeholder="输入想核查的评分问题"
              disabled={busy}
            />
            <Button
              type="primary"
              htmlType="submit"
              disabled={!question.trim() || busy}
              loading={busy}
            >
              开始分析
            </Button>
          </div>
        </form>
      )}
      {status && !task?.error_code && (
        <div className={`ai-task-actions${analyzing ? ' ai-task-running' : ''}`}>
          <span className="ai-task-status" role="status">
            {analyzing && <span className="ai-analysis-indicator" aria-hidden="true" />}
            {status}
          </span>
          {running && task && (
            <Button
              disabled={busy}
              onClick={() =>
                void operate(async (signal) => {
                  const value = checkExecution(
                    await client.cancelAiTask(batch, task.task_id, signal),
                  );
                  if (!signal.aborted) setTask(value);
                })
              }
            >
              停止分析
            </Button>
          )}
        </div>
      )}
      {error && (
        <RequestError
          error={error}
          retry={
            task
              ? () =>
                  void operate(async (signal) => {
                    const value = checkExecution(await client.aiTask(batch, task.task_id, signal));
                    if (!signal.aborted) setTask(value);
                  })
              : undefined
          }
        />
      )}
      {restoreNotice && !error && !task && <Alert showIcon type="info" title={restoreNotice} />}
      {task?.error_code && !error && (
        <Alert
          showIcon
          type={task.state === 'cancelled' ? 'info' : 'warning'}
          title={taskFailure?.message ?? taskLabel}
          description={
            taskFailure?.action ??
            (task.state === 'failed' || task.state === 'timed_out'
              ? '本轮分析未完成，可切换其他测评批次。'
              : undefined)
          }
        />
      )}
      {task?.result && (
        <>
          {facts.length > 0 && (
            <section className="ai-findings" aria-label="分析发现">
              <h3>重点发现</h3>
              <div className="ai-fact-grid">{facts.slice(0, 2).map(renderFact)}</div>
              {facts.length > 2 && (
                <details className="ai-foldout">
                  <summary>其他发现（{facts.length - 2} 条）</summary>
                  <div className="ai-fact-grid">{facts.slice(2).map(renderFact)}</div>
                </details>
              )}
            </section>
          )}
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
          {projection && profiles.length > 0 && (
            <section className="ai-profile-section" aria-labelledby="ai-profiles-heading">
              <h2 id="ai-profiles-heading">群体画像</h2>
              <div className="ai-profile-grid">
                {profiles.map((profile) => {
                  const focus = profile.dimensions
                    .filter((dimension) => !dimension.condition_defined)
                    .sort(
                      (left, right) =>
                        Math.abs(exactToNumber(right.difference)) -
                        Math.abs(exactToNumber(left.difference)),
                    )[0];
                  return (
                    <section
                      className="discovery-card ai-profile-card"
                      data-testid="ai-profile-card"
                      key={profile.profile_id}
                    >
                      <h3>{profile.title}</h3>
                      {focus && (
                        <div className="ai-profile-focus">
                          <button
                            type="button"
                            className="ai-profile-dimension-action"
                            onClick={() => setProfileEvidence({ profile, dimension: focus })}
                          >
                            查看{focus.label}评分依据
                          </button>
                          <strong>均分差 {exactScore(focus.difference)} 分</strong>
                          <span>
                            本组 {exactScore(focus.target.mean)} 分 · 对照组{' '}
                            {exactScore(focus.remainder.mean)} 分
                          </span>
                        </div>
                      )}
                      <p>{profile.suggested_next_action}</p>
                      <details className="ai-foldout">
                        <summary>查看完整五维评分</summary>
                        <table
                          className="ai-profile-dimensions"
                          aria-label={`${profile.title}评分对照（分）`}
                        >
                          <thead>
                            <tr>
                              <th scope="col">评分维度</th>
                              <th scope="col">本组</th>
                              <th scope="col">对照组</th>
                              <th scope="col">均分差</th>
                            </tr>
                          </thead>
                          <tbody>
                            {profile.dimensions.map((dimension) => (
                              <tr key={dimension.dimension_code}>
                                <th scope="row">
                                  <button
                                    type="button"
                                    className="ai-profile-dimension-action"
                                    onClick={() => setProfileEvidence({ profile, dimension })}
                                    aria-label={`查看${dimension.label}评分依据${dimension.condition_defined ? '（分组条件）' : ''}`}
                                  >
                                    <span>{dimension.label}</span>
                                    <span aria-hidden="true">›</span>
                                  </button>
                                </th>
                                <td>{exactScore(dimension.target.mean)}</td>
                                <td>{exactScore(dimension.remainder.mean)}</td>
                                <td>{exactScore(dimension.difference)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </details>
                      <div className="ai-fact-actions">
                        <Button
                          onClick={() => {
                            setConditions(profileConditions(profile));
                            router.push('/explorer');
                          }}
                        >
                          继续探索该群体
                        </Button>
                        <details className="ai-profile-note">
                          <summary>分组与使用说明</summary>
                          <p>{profile.common_pattern}</p>
                          <p>{profile.why_show}</p>
                          <p>
                            报告仅收录教师确认的发现。评分明细须满足至少 {projection.minimum_cell}{' '}
                            人的隐私要求。
                          </p>
                        </details>
                      </div>
                    </section>
                  );
                })}
              </div>
            </section>
          )}
          {projection &&
            profiles.length === 0 &&
            task.result.stop_reason !== 'no_supported_findings' && (
              <p className="ai-profile-empty" role="status">
                本轮未发现符合条件的群体画像
              </p>
            )}
          {!!reportFacts.length && (
            <Button className="ai-review-submit" type="primary" loading={busy} onClick={review}>
              前往报告核查
            </Button>
          )}
          <details className="ai-foldout ai-technical">
            <summary>技术详情</summary>
            <dl>
              <dt>执行来源</dt>
              <dd>
                {task.execution_origin === 'qwen_live'
                  ? 'Qwen'
                  : task.execution_origin === 'http_mock'
                    ? '模拟分析（未调用真实模型）'
                    : '未执行'}
              </dd>
              <dt>模型请求</dt>
              <dd>{task.model_requests} 次</dd>
              <dt>核查工具</dt>
              <dd>{task.tool_attempts} 次</dd>
            </dl>
          </details>
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
      {task && profileEvidence && (
        <ProfileScoreDrawer
          batch={batch}
          task={task}
          profile={profileEvidence.profile}
          dimension={profileEvidence.dimension}
          onClose={() => setProfileEvidence(null)}
        />
      )}
    </Panel>
  );
}

function FactCard({
  fact,
  onEvidence,
  onExplore,
}: {
  fact: AiFact;
  onEvidence: () => void;
  onExplore: () => void;
}) {
  const primary = primaryMetrics(fact);
  const conclusion = factConclusion(fact);
  const secondary = fact.metrics.filter(
    (metric) => metricLabels[metric.key] && !primary.includes(metric),
  );
  return (
    <section className="discovery-card ai-compact-fact" data-testid="ai-fact-card">
      <h4>{factTitle(fact)}</h4>
      {conclusion && <p className="ai-fact-conclusion">{conclusion}</p>}
      {!!fact.conditions.length && <p>{conditionText(fact.conditions)}</p>}
      <dl className="focus-metrics" aria-label="发现指标">
        {primary.map((metric) => (
          <div key={metric.key}>
            <dt>{metricLabels[metric.key]}</dt>
            <dd>
              <strong>{factMetricValue(metric)}</strong>
            </dd>
          </div>
        ))}
      </dl>
      {secondary.length > 0 && (
        <details className="ai-foldout">
          <summary>查看统计明细</summary>
          <dl>
            {secondary.map((metric) => (
              <div key={metric.key}>
                <dt>{metricLabels[metric.key]}</dt>
                <dd>{factMetricValue(metric)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      <div className="ai-fact-actions">
        {!!fact.source_refs.length && fact.source_verified && (
          <Button onClick={onEvidence}>查看评分依据</Button>
        )}
        {fact.exploration_query && fact.source_verified && (
          <Button onClick={onExplore}>继续探索</Button>
        )}
      </div>
    </section>
  );
}

function ProfileScoreDrawer({
  batch,
  task,
  profile,
  dimension,
  onClose,
}: {
  batch: ApiBatch;
  task: AiTask;
  profile: AiProfile;
  dimension: AiProfileDimension;
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const [side, setSide] = useState<'target' | 'remainder'>('target');
  const [offset, setOffset] = useState(0);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) =>
      client.aiProfileScores(
        batch,
        task,
        profile.profile_id,
        dimension.dimension_code,
        side,
        offset,
        signal,
      ),
    [client, batch, task, profile.profile_id, dimension.dimension_code, side, offset],
  );
  const resource = useRemote(
    `ai-profile-scores:${task.task_id}:${profile.profile_id}:${dimension.dimension_code}:${side}:${offset}`,
    load,
  );
  return (
    <Drawer
      title={`${dimension.label} · 评分依据`}
      open
      onClose={onClose}
      size={680}
      className="ai-evidence-drawer"
    >
      <Segmented<'target' | 'remainder'>
        aria-label="选择画像群体"
        block
        size="large"
        value={side}
        onChange={(value) => {
          setSide(value);
          setOffset(0);
        }}
        options={[
          { value: 'target', label: '本组' },
          {
            value: 'remainder',
            label: '对照组',
          },
        ]}
      />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {resource.data?.detail_status === 'privacy_limited' && (
        <Alert showIcon type="warning" title="受隐私保护，无法查看这组评分明细" />
      )}
      {resource.data?.detail_status === 'available' && (
        <>
          <dl className="focus-metrics ai-profile-evidence-summary">
            <div>
              <dt>均分</dt>
              <dd>
                <strong>{exactScore(resource.data.mean)}</strong> 分
              </dd>
            </div>
            <div>
              <dt>有效评分</dt>
              <dd>
                <strong>{resource.data.valid_count}</strong> 条
              </dd>
            </div>
          </dl>
          <Table
            rowKey="evidence_id"
            dataSource={resource.data.items}
            size="small"
            pagination={false}
            columns={[
              { title: '记录', render: (_, _row, index) => offset + index + 1 },
              { title: '评分', render: (_, row) => exactScore(row.score) },
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
            total={resource.data.total_count ?? 0}
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
    <Drawer title="评分依据" open onClose={onClose} size={620} className="ai-evidence-drawer">
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
