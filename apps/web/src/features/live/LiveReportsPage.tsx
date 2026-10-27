'use client';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { NoBatch } from './LiveStatus';
import { conditionText } from '../../services/edu-api/view-model';
import AnalysisActions from './AnalysisActions';
export default function LiveReportsPage() {
  const { batch, conditions, reportDraft, setReportDraft } = useDataMode();
  if (!batch) return <NoBatch />;
  const key = `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
  const draft = reportDraft?.key === key ? reportDraft : null;
  const spec = draft?.spec ?? {
    snapshot_id: batch.snapshot_id,
    conditions,
    include_dimensions: false,
  };
  return (
    <div className="page-stack reports-page">
      <Panel title="本次报告内容" subtitle={batch.title}>
        <p>分析范围：{conditionText(spec.conditions)}</p>
        {draft?.discovery?.discovery_ids.length ? (
          <>
            <p>已加入待保存的报告 · {draft.discovery.discovery_ids.length} 项发现</p>
            <ul>
              {draft.names
                .filter((item) => draft.discovery?.discovery_ids.includes(item.id))
                .map((item) => (
                  <li key={item.id}>{item.title}</li>
                ))}
            </ul>
          </>
        ) : (
          <p className="muted">当前可保存测评概况；没有选定发现时不会生成诊断结论。</p>
        )}
        {draft?.discovery?.discovery_ids.length ? (
          <button
            className="text-link"
            onClick={() =>
              setReportDraft({
                ...draft,
                discovery: { ...draft.discovery!, discovery_ids: [] },
                names: [],
              })
            }
          >
            清除待保存发现
          </button>
        ) : null}
      </Panel>
      <AnalysisActions key={key} batch={batch} spec={spec} discovery={draft?.discovery} />
    </div>
  );
}
