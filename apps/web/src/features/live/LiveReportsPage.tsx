'use client';
import { useDataMode } from '../../app/data-mode';
import { Button } from 'antd';
import { Panel } from '../../components/ui';
import { NoBatch } from './LiveStatus';
import { conditionText } from '../../services/edu-api/view-model';
import AnalysisActions from './AnalysisActions';
export default function LiveReportsPage() {
  const { batch, conditions, reportDraft, setReportDraft } = useDataMode();
  if (!batch) return <NoBatch />;
  if (batch.recognized_items) return <Panel>当前测评暂不支持生成或导出分析报告。</Panel>;
  const key = `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
  const draft = reportDraft?.key === key ? reportDraft : null;
  const spec = draft?.spec ?? {
    snapshot_id: batch.snapshot_id,
    conditions,
    include_dimensions: false,
  };
  return (
    <div className="page-stack reports-page">
      <AnalysisActions
        key={key}
        batch={batch}
        spec={spec}
        discovery={draft?.discovery}
        draftContent={
          <Panel title="本次报告内容" subtitle={batch.title}>
            <p>分析范围：{conditionText(spec.conditions)}</p>
            {draft?.discovery?.discovery_ids.length ? (
              <>
                <p>已加入待保存的报告 · {draft.discovery.discovery_ids.length} 项发现</p>
                <ul className="report-draft-list">
                  {draft.names
                    .filter((item) => draft.discovery?.discovery_ids.includes(item.id))
                    .map((item) => (
                      <li key={item.id}>
                        <span>{item.title}</span>
                        <Button
                          size="small"
                          type="text"
                          aria-label={`移除 ${item.title}`}
                          onClick={() =>
                            setReportDraft({
                              ...draft,
                              discovery: {
                                ...draft.discovery!,
                                discovery_ids: draft.discovery!.discovery_ids.filter(
                                  (id) => id !== item.id,
                                ),
                              },
                              names: draft.names.filter((name) => name.id !== item.id),
                            })
                          }
                        >
                          移除
                        </Button>
                      </li>
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
        }
      />
    </div>
  );
}
