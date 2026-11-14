'use client';
import { useCallback, useMemo, useState } from 'react';
import { useDataMode } from '../../app/data-mode';
import { Button, Checkbox } from 'antd';
import { useRouter } from 'next/navigation';
import { Panel } from '../../components/ui';
import { LoadingPanel, NoBatch, RequestError } from './LiveStatus';
import { conditionText } from '../../services/edu-api/view-model';
import { ApiError } from '../../services/edu-api/client';
import type { ApiBatch } from '../../services/edu-api/types';
import type { DiscoverySpec } from '../../services/edu-api/discovery-types';
import AnalysisActions from './AnalysisActions';
import { useRemote } from './useRemote';

export default function LiveReportsPage() {
  const { batch, conditions, openAssessments } = useDataMode();
  const router = useRouter();
  if (!batch) return <NoBatch />;
  if (batch.recognized_items)
    return (
      <Panel>
        <p>当前测评暂不支持生成或导出分析报告。</p>
        <Button
          type="primary"
          onClick={() => {
            openAssessments(null);
            router.push('/insights');
          }}
        >
          返回学情诊断
        </Button>
      </Panel>
    );
  const key = `${batch.batch_id}:${batch.snapshot_id}:${JSON.stringify(conditions)}`;
  return <ReportWorkspace key={key} batch={batch} contextKey={key} />;
}

function ReportWorkspace({ batch, contextKey }: { batch: ApiBatch; contextKey: string }) {
  const { client, conditions, reportDraft, activeReport } = useDataMode();
  const draft = reportDraft?.key === contextKey ? reportDraft : null;
  const spec = draft?.spec ?? {
    snapshot_id: batch.snapshot_id,
    conditions,
    include_dimensions: false,
  };
  const scope = useMemo<DiscoverySpec>(
    () => ({
      snapshot_id: batch.snapshot_id,
      scope_conditions: draft?.spec.conditions ?? conditions,
      profile: 'edu.discovery.profile.v1',
    }),
    [batch.snapshot_id, draft?.spec.conditions, conditions],
  );
  const [excluded, setExcluded] = useState<string[]>([]);
  const active =
    activeReport?.batchId === batch.batch_id && activeReport?.snapshotId === batch.snapshot_id;
  const load = useCallback(
    async (signal: AbortSignal) => {
      const result = await client.discoveries(batch, scope, 0, signal, 50);
      if (result.items.length !== result.retained_count)
        throw new ApiError(502, 'INVALID_API_RESPONSE');
      return {
        inputKey: result.scope.input_key,
        items: result.items
          .filter((item) => item.status !== 'insufficient_evidence')
          .map((item) => ({ id: item.discovery_id, title: item.title })),
      };
    },
    [client, batch, scope],
  );
  const candidates = useRemote(
    !draft?.discovery && !active ? `report-discoveries:${contextKey}` : null,
    load,
  );
  const items = draft?.discovery ? draft.names : (candidates.data?.items ?? []);
  const inputKey = draft?.discovery?.input_key ?? candidates.data?.inputKey;
  const included = items.filter((item) => !excluded.includes(item.id));
  const discovery = inputKey
    ? { input_key: inputKey, discovery_ids: included.map((item) => item.id) }
    : undefined;
  return (
    <div className="page-stack reports-page">
      <AnalysisActions
        batch={batch}
        spec={spec}
        discovery={discovery}
        blocked={
          candidates.loading
            ? '正在读取发现…'
            : candidates.error
              ? '暂时无法读取报告内容。'
              : undefined
        }
        draftContent={
          <Panel title="本次报告内容" subtitle={batch.title}>
            <p>分析范围：{conditionText(spec.conditions)}</p>
            {candidates.loading && <LoadingPanel />}
            {candidates.error && <RequestError error={candidates.error} retry={candidates.retry} />}
            {items.length > 0 && (
              <ul className="report-draft-list">
                {items.map((item) => (
                  <li key={item.id}>
                    <Checkbox
                      value={item.id}
                      checked={!excluded.includes(item.id)}
                      onChange={(event) =>
                        setExcluded((ids) =>
                          event.target.checked
                            ? ids.filter((id) => id !== item.id)
                            : [...ids, item.id],
                        )
                      }
                    >
                      {item.title}
                    </Checkbox>
                  </li>
                ))}
              </ul>
            )}
            {!items.length && !candidates.loading && !candidates.error && (
              <p className="muted">当前范围可保存测评概况。</p>
            )}
          </Panel>
        }
      />
    </div>
  );
}
