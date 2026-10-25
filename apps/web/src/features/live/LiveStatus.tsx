'use client';
import { Alert, Button, Empty, Spin } from 'antd';
import { ApiError } from '../../services/edu-api/client';
import { Panel } from '../../components/ui';
import { useDataMode } from '../../app/data-mode';
export function RequestError({ error, retry }: { error: ApiError; retry?: () => void }) {
  const { client, batch, selectBatch } = useDataMode();
  async function refreshSnapshot() {
    if (batch) {
      try {
        selectBatch(await client.batch(batch.batch_id));
      } catch {
        selectBatch(null);
      }
    }
  }
  return (
    <Alert
      className="live-error"
      showIcon
      type="error"
      title={error.message}
      description={
        <>
          <span>错误码：{error.code}</span>
          {error.requestId && <span> · 请求编号：{error.requestId}</span>}
        </>
      }
      action={
        error.code === 'SNAPSHOT_MISMATCH' ? (
          <Button onClick={refreshSnapshot}>刷新批次</Button>
        ) : retry ? (
          <Button onClick={retry}>重试</Button>
        ) : undefined
      }
    />
  );
}
export function LoadingPanel() {
  return (
    <Panel className="live-loading">
      <Spin />
      <p className="muted">正在读取服务端数据…</p>
    </Panel>
  );
}
export function NoBatch() {
  return (
    <Panel>
      <Empty description="暂无已选择的授权批次，请选择批次或到测评数据页上传。" />
    </Panel>
  );
}
