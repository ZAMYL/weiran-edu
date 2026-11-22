'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Alert, Button, Drawer, Empty, Pagination, Select } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { ApiError } from '../../services/edu-api/client';
import { LoadingPanel, RequestError } from './LiveStatus';
import { useRemote } from './useRemote';

const library = '/data/answer-sheets';
const maxFiles = 20;
const maxBytes = 10 * 1024 * 1024;

export function AnswerSheetLibrary() {
  const router = useRouter();
  const { client, scopes } = useDataMode();
  const [page, setPage] = useState(1);
  const [files, setFiles] = useState<File[]>([]);
  const [scope, setScope] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError>();
  const upload = useRef<AbortController | null>(null);
  const importScopes = scopes.filter((item) => item.can_import);
  const selectedScope = importScopes.length === 1 ? importScopes[0]?.data_scope_id : scope;
  const load = useCallback(
    (signal: AbortSignal) => client.answerSheets((page - 1) * 20, signal),
    [client, page],
  );
  const history = useRemote(String(page), load);
  const invalid =
    files.length > maxFiles ||
    files.some((file) => !/\.zip$/i.test(file.name)) ||
    files.reduce((total, file) => total + file.size, 0) > maxBytes;
  useEffect(() => () => upload.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (upload.current || invalid || !files.length || !selectedScope) return;
    const controller = new AbortController();
    upload.current = controller;
    setBusy(true);
    setError(undefined);
    const form = new FormData();
    for (const file of files) form.append('file', file);
    form.append('data_scope_id', selectedScope);
    try {
      const result = await client.uploadAnswerSheets(form, controller.signal);
      router.push(`${library}/${encodeURIComponent(result.assessment_id)}`);
    } catch (cause) {
      if (!controller.signal.aborted)
        setError(cause instanceof ApiError ? cause : new ApiError(502, 'INVALID_API_RESPONSE'));
    } finally {
      if (!controller.signal.aborted) {
        upload.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <div className="page-stack">
      <Panel title="上传答题卡">
        <form className="answer-sheet-upload" onSubmit={submit}>
          {importScopes.length > 1 && (
            <Select
              aria-label="授权数据来源"
              placeholder="选择数据来源"
              value={scope}
              disabled={busy}
              onChange={setScope}
              options={importScopes.map((item) => ({
                value: item.data_scope_id,
                label: item.label,
              }))}
            />
          )}
          {!importScopes.length && <Alert type="warning" title="当前账户没有上传权限" />}
          <label className="answer-sheet-file">
            选择 ZIP 文件
            <input
              type="file"
              accept=".zip"
              multiple
              disabled={busy || !importScopes.length}
              onChange={(event) => {
                setFiles(Array.from(event.currentTarget.files ?? []));
                setError(undefined);
              }}
            />
          </label>
          <p className="muted">最多 20 个 ZIP，总大小不超过 10 MB。</p>
          {files.length > 1 && <p>已选 {files.length} 个文件</p>}
          {invalid && <Alert type="error" title="请选择符合数量和大小限制的 ZIP 文件" />}
          {error && <RequestError error={error} />}
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={!selectedScope || !files.length || invalid}
          >
            {busy ? '正在导入' : '上传并查看结果'}
          </Button>
        </form>
      </Panel>
      <Panel title="历史答题卡">
        {history.loading && <LoadingPanel />}
        {history.error && <RequestError error={history.error} retry={history.retry} />}
        {history.data &&
          (history.data.items.length ? (
            <>
              <ul className="answer-sheet-list">
                {history.data.items.map((item) => (
                  <li key={item.assessment_id}>
                    <Link href={`${library}/${encodeURIComponent(item.assessment_id)}`}>
                      {item.title}
                    </Link>
                    <span>
                      {item.student_count} 人 · {item.problem_count} 题
                      {item.review_state === 'needs_review' ? ' · 待核查' : ''}
                    </span>
                  </li>
                ))}
              </ul>
              <Pagination
                current={page}
                pageSize={20}
                total={history.data.total}
                onChange={setPage}
                showSizeChanger={false}
                hideOnSinglePage
              />
            </>
          ) : (
            <Empty description="暂无答题卡测评" />
          ))}
      </Panel>
    </div>
  );
}

export function AnswerSheetDetails({ assessmentId }: { assessmentId: string }) {
  const { client } = useDataMode();
  const [studentId, setStudentId] = useState<string | null>(null);
  const load = useCallback(
    (signal: AbortSignal) => client.answerSheet(assessmentId, signal),
    [client, assessmentId],
  );
  const result = useRemote(assessmentId, load);
  const snapshot = result.data?.overview.source_snapshot_id ?? null;
  const loadEvidence = useCallback(
    (signal: AbortSignal) => client.answerSheetEvidence(assessmentId, studentId!, snapshot, signal),
    [client, assessmentId, studentId, snapshot],
  );
  const evidence = useRemote(result.data ? studentId : null, loadEvidence);
  const stale =
    evidence.error?.code === 'SOURCE_CHANGED' || evidence.error?.code === 'ORIGINAL_UNAVAILABLE';
  const data = result.data;
  const selectedStudent = data?.students.find((item) => item.student_pk === studentId);
  const attemptTimes = [
    selectedStudent?.started_at && `开始 ${selectedStudent.started_at}`,
    selectedStudent?.finished_at && `结束 ${selectedStudent.finished_at}`,
  ].filter(Boolean);

  return (
    <div className="page-stack">
      <Link href={library}>返回编程答题卡</Link>
      {result.loading && <LoadingPanel />}
      {result.error && <RequestError error={result.error} retry={result.retry} />}
      {data && (
        <>
          <Panel title={data.overview.title}>
            {data.overview.counts_provisional && (
              <Alert type="warning" title="这份测评有待核查项，人数和统计为暂定结果。" />
            )}
            <dl className="answer-sheet-metrics">
              <div>
                <dt>参与人数</dt>
                <dd>{data.overview.student_count}</dd>
              </div>
              <div>
                <dt>题目</dt>
                <dd>{data.overview.problem_count}</dd>
              </div>
              <div>
                <dt>来源文件</dt>
                <dd>{data.overview.assets.length}</dd>
              </div>
            </dl>
            <details>
              <summary>查看资料来源</summary>
              <ul>
                {data.overview.assets.map((asset) => (
                  <li key={asset.asset_id}>
                    {asset.entry}
                    {asset.storage_state !== 'stored' ? '（原件不可用）' : ''}
                  </li>
                ))}
              </ul>
              {data.overview.materials.map((material, index) => (
                <p key={`${material.asset_id ?? 'unknown'}:${material.entry}:${index}`}>
                  {data.overview.assets.find((asset) => asset.asset_id === material.asset_id)
                    ?.entry ?? '来源包未确定'}{' '}
                  · {material.entry}
                  {material.needs_confirmation && '（待核查）'}
                </p>
              ))}
              {data.overview.diagnostics
                .filter((item) => item.code !== 'TEST_COLUMNS_INFERRED')
                .map((item, index) => (
                  <p key={`${item.code}:${index}`}>
                    {item.entry && `${item.entry} · `}
                    {item.message}
                  </p>
                ))}
            </details>
          </Panel>
          <Panel title="群体错误统计">
            {data.patterns.length ? (
              <div className="answer-sheet-patterns">
                {data.patterns.map((item) => (
                  <section key={item.ordinal}>
                    <h3>{item.problem_label}</h3>
                    <p>
                      {item.submission_count} 份提交
                      {item.failure_rate !== null &&
                        ` · 有明确结论的测试点失败率 ${(item.failure_rate * 100).toFixed(1)}%`}
                    </p>
                    {item.verdict_counts.length > 0 && (
                      <p>
                        判题分布：
                        {item.verdict_counts
                          .map((entry) => `${entry.status || '未知'} ${entry.count}`)
                          .join('、')}
                      </p>
                    )}
                    <p>
                      {item.worst_test_points.length
                        ? `失败测试点：${item.worst_test_points.map((point) => `第 ${point.test_index} 点（${point.failure_count} 次）`).join('、')}`
                        : '暂无明确失败的测试点'}
                    </p>
                  </section>
                ))}
              </div>
            ) : (
              <Empty description="暂无题目统计" />
            )}
          </Panel>
          <Panel title="参与者">
            {data.students.length ? (
              <ul className="answer-sheet-list">
                {data.students.map((student) => (
                  <li key={student.student_pk}>
                    <Button type="link" onClick={() => setStudentId(student.student_pk)}>
                      {student.display_name || student.external_key || '查看个人证据'}
                    </Button>
                    {student.display_name && student.external_key && (
                      <span>{student.external_key}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <Empty description="暂无参与者" />
            )}
          </Panel>
        </>
      )}
      <Drawer
        title="个人答题证据"
        open={studentId !== null}
        onClose={() => setStudentId(null)}
        size="min(650px, 100vw)"
        rootClassName="answer-sheet-evidence"
      >
        {evidence.loading && <LoadingPanel />}
        {evidence.error &&
          (stale ? (
            <Alert
              type="warning"
              title={evidence.error.code === 'SOURCE_CHANGED' ? '资料来源已变化' : '原件当前不可用'}
              action={
                <Button
                  onClick={() => {
                    setStudentId(null);
                    result.retry();
                  }}
                >
                  刷新测评
                </Button>
              }
            />
          ) : (
            <RequestError error={evidence.error} retry={evidence.retry} />
          ))}
        {evidence.data && (
          <div className="page-stack">
            <div>
              <h2>{evidence.data.display_name || evidence.data.external_key}</h2>
              {evidence.data.display_name && evidence.data.external_key && (
                <p>{evidence.data.external_key}</p>
              )}
              {attemptTimes.length > 0 && <p>{attemptTimes.join(' · ')}</p>}
            </div>
            {!evidence.data.submissions.length && <Empty description="暂无提交证据" />}
            {evidence.data.submissions.map((submission) => (
              <Panel key={submission.ordinal} title={submission.problem_label}>
                {submission.verdict && <p>判题结果：{submission.verdict}</p>}
                {submission.source?.entry && (
                  <p className="muted">
                    {[
                      data?.overview.assets.find(
                        (asset) => asset.asset_id === submission.source?.asset_id,
                      )?.entry,
                      submission.source.entry,
                      submission.source.source_index != null
                        ? `题块 ${submission.source.source_index}`
                        : null,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>
                )}
                {submission.code_state === 'PRESENT' && submission.code && (
                  <details open>
                    <summary>
                      代码{submission.language && ` · ${submission.language}`}
                      {submission.code_truncated && '（部分）'}
                    </summary>
                    <pre className="answer-sheet-code">
                      <code>{submission.code}</code>
                    </pre>
                  </details>
                )}
                {submission.compiler_output && (
                  <details>
                    <summary>编译输出{submission.compiler_output_truncated && '（部分）'}</summary>
                    <pre className="answer-sheet-code">{submission.compiler_output}</pre>
                  </details>
                )}
                {submission.observations.length > 0 && (
                  <details open>
                    <summary>测试点</summary>
                    <ul>
                      {submission.observations.map((point, index) => (
                        <li key={`${point.test_index}:${point.source_row ?? index}`}>
                          {[
                            point.test_index != null ? `第 ${point.test_index} 点` : null,
                            point.status,
                            point.duration_text,
                            point.memory_text,
                            point.source_row != null ? `原表第 ${point.source_row} 行` : null,
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </Panel>
            ))}
          </div>
        )}
      </Drawer>
    </div>
  );
}
