'use client';
import { tokens } from '../../app/theme';
import { Disclosure } from '../../components/Disclosure';
import { useCallback, useState } from 'react';
import { Descriptions, Drawer, Select, Typography } from 'antd';
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
        <Descriptions
          className="source-details"
          size="small"
          column={1}
          bordered
          style={{ marginTop: tokens.size }}
          items={[
            {
              key: 'item',
              label: '题目',
              children: batch.recognized_items?.find((item) => item.item_id === itemId)
                ?.display_name,
            },
            { key: 'participant', label: '参评记录', children: evidence.participant_id },
            { key: 'quality', label: '评分状态', children: qualityLabels[evidence.quality_state] },
            {
              key: 'score',
              label: '得分',
              children: evidence.exact ? scoreDisplay(exactToNumber(evidence.exact)) : '—',
            },
            {
              key: 'source',
              label: '原文件位置',
              children: `${evidence.source.sheet}!${evidence.source.cell}`,
            },
          ]}
        />
      )}
      {evidence && (
        <Disclosure className="technical-details" label={<>来源校验信息</>}>
          {evidence.exact && (
            <Typography.Text style={{ display: 'block', marginBottom: tokens.sizeXXS }}>
              精确值 {evidence.exact.numerator}/{evidence.exact.denominator}
            </Typography.Text>
          )}
          <Typography.Text type="secondary" style={{ overflowWrap: 'anywhere', fontSize: 12 }}>
            {evidence.source.file_sha256}
          </Typography.Text>
        </Disclosure>
      )}
    </Drawer>
  );
}
