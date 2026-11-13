'use client';

import { useCallback, useState } from 'react';
import { Button, Pagination, Select } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { useRemote } from './useRemote';

export default function LiveAssessmentPicker() {
  const { client, batch, connected, revision, selectBatch } = useDataMode();
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => client.batches(offset, 20, signal),
    [client, offset],
  );
  const { data, error, loading, retry } = useRemote(
    connected ? `batch-list:${revision}:${offset}` : null,
    load,
  );
  const items = data?.items ?? [];
  const options = items.map((item) => ({ label: item.title, value: item.batch_id }));
  if (batch && !options.some((option) => option.value === batch.batch_id))
    options.unshift({ label: batch.title, value: batch.batch_id });

  return (
    <div className="live-batch-picker">
      <Select
        className="batch-select"
        aria-label="当前测评批次"
        value={batch?.batch_id}
        placeholder={connected ? '选择可查看的测评' : '连接账户后选择测评'}
        disabled={!connected}
        loading={loading}
        options={options}
        onChange={(value) => {
          const target = items.find((item) => item.batch_id === value);
          if (target) selectBatch(target);
        }}
        popupRender={(menu) => (
          <>
            {menu}
            {error && (
              <div className="batch-picker-error">
                <Button type="link" onClick={retry}>
                  测评读取失败，重试
                </Button>
              </div>
            )}
            {data && data.total_count > 20 && (
              <Pagination
                simple
                current={Math.floor(offset / 20) + 1}
                total={data.total_count}
                pageSize={20}
                showSizeChanger={false}
                onChange={(page) => setOffset((page - 1) * 20)}
              />
            )}
          </>
        )}
      />
    </div>
  );
}
