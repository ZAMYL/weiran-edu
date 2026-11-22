'use client';
import { Disclosure } from '../../components/Disclosure';
import { Alert, Button, Empty, Spin } from 'antd';
import Link from 'next/link';
import { scoreHistory, scoreUpload } from '../../app/workspace-routes';
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
        <Disclosure label={<>错误详情</>}>
          <span>错误码：{error.code}</span>
          {error.requestId && <span> · 请求编号：{error.requestId}</span>}
        </Disclosure>
      }
      action={
        error.code === 'SNAPSHOT_MISMATCH' ? (
          <Button onClick={refreshSnapshot}>刷新测评</Button>
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
      <p className="muted">正在读取数据…</p>
    </Panel>
  );
}
export function NoBatch() {
  return (
    <Panel>
      <Empty description="暂无可查看的测评">
        <Link href={scoreUpload}>上传测评资料</Link>
        <span aria-hidden="true"> · </span>
        <Link href={scoreHistory}>历史测评</Link>
      </Empty>
    </Panel>
  );
}
