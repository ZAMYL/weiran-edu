'use client';
import { useCallback, useState } from 'react';
import { Drawer, Select } from 'antd';
import { useDataMode } from '../../app/data-mode';
import type { ApiBatch } from '../../services/edu-api/types';
import { exactToNumber } from '../../services/edu-api/exact';
import { qualityLabels } from '../../services/edu-api/question-types';
import { scoreDisplay } from '../../services/edu-api/view-model';
import { useRemote } from './useRemote';
import { LoadingPanel, RequestError } from './LiveStatus';

export default function QuestionEvidenceDrawer({
  batch,
  itemId,
  evidenceIds,
  onClose,
}: {
  batch: ApiBatch;
  itemId: string;
  evidenceIds: string[];
  onClose: () => void;
}) {
  const { client } = useDataMode();
  const [selected, setSelected] = useState(evidenceIds[0]);
  const load = useCallback(
    (signal: AbortSignal) => client.itemEvidence(batch, selected!, itemId, signal),
    [client, batch, itemId, selected],
  );
  const resource = useRemote(
    selected ? `item-evidence:${batch.batch_id}:${batch.snapshot_id}:${selected}` : null,
    load,
  );
  const evidence = resource.data;
  return (
    <Drawer title="评分依据" open onClose={onClose} size={540}>
      <Select
        aria-label="选择评分记录"
        value={selected}
        style={{ width: '100%' }}
        options={evidenceIds.map((id, index) => ({ value: id, label: `评分记录 ${index + 1}` }))}
        onChange={setSelected}
      />
      {resource.loading && <LoadingPanel />}
      {resource.error && <RequestError error={resource.error} retry={resource.retry} />}
      {evidence && (
        <dl className="source-details">
          <dt>题目</dt>
          <dd>{batch.recognized_items?.find((item) => item.item_id === itemId)?.display_name}</dd>
          <dt>参评记录</dt>
          <dd>{evidence.participant_id}</dd>
          <dt>评分状态</dt>
          <dd>{qualityLabels[evidence.quality_state]}</dd>
          <dt>得分</dt>
          <dd>{evidence.exact ? scoreDisplay(exactToNumber(evidence.exact)) : '—'}</dd>
          <dt>原文件位置</dt>
          <dd>
            {evidence.source.sheet}!{evidence.source.cell}
          </dd>
        </dl>
      )}
      {evidence && (
        <details className="technical-details">
          <summary>来源校验信息</summary>
          {evidence.exact && (
            <p>
              精确值 {evidence.exact.numerator}/{evidence.exact.denominator}
            </p>
          )}
          <p style={{ overflowWrap: 'anywhere' }}>{evidence.source.file_sha256}</p>
        </details>
      )}
    </Drawer>
  );
}
