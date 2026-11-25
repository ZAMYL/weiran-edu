'use client';

import { useCallback, useState } from 'react';
import {
  Button,
  Card,
  Col,
  Empty,
  Flex,
  Row,
  Skeleton,
  Space,
  Statistic,
  Table,
  Typography,
  theme,
} from 'antd';
import { BarChartOutlined, FileZipOutlined, UploadOutlined } from '@ant-design/icons';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useDataMode } from './data-mode';
import {
  answerAnalysisPath,
  answerHistory,
  answerUpload,
  scoreHistory,
  scoreUpload,
} from './workspace-routes';
import { useRemote } from '../features/live/useRemote';
import { RequestError } from '../features/live/LiveStatus';
import type { ApiBatch } from '../services/edu-api/types';

export default function WorkspaceDashboard() {
  const router = useRouter();
  const { token } = theme.useToken();
  const { client, batch, scopes, assessmentTitle, batchListRevision, selectBatch } = useDataMode();
  const canImport = scopes.some((scope) => scope.can_import);
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => client.batches(offset, 5, signal),
    [client, offset],
  );
  const loadAnswerSheets = useCallback(
    (signal: AbortSignal) => client.answerSheets(0, signal),
    [client],
  );
  const answerSheets = useRemote('dashboard-answer-sheets', loadAnswerSheets);
  const pending = answerSheets.data?.items.filter((item) => item.review_state === 'needs_review');
  const recent = useRemote(`dashboard-batches:${batchListRevision}:${offset}`, load);
  const activeBatches = recent.data?.total_count;
  const gutter: [number, number] = [token.margin, token.margin];

  return (
    <Flex vertical gap={token.margin}>
      <Row gutter={gutter}>
        <Col xs={24} sm={8}>
          <Card style={{ height: '100%' }}>
            <Statistic title="当前测评" value={batch ? assessmentTitle(batch) : '未选择'} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card style={{ height: '100%' }}>
            <Statistic title="使用中的测评" value={activeBatches ?? '—'} loading={recent.loading} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card style={{ height: '100%' }}>
            <Statistic
              title="待核查答题卡"
              value={pending?.length ?? '—'}
              loading={answerSheets.loading}
            />
            <Typography.Text type="secondary">最近 20 份</Typography.Text>
          </Card>
        </Col>
      </Row>
      <Row gutter={gutter}>
        <Col xs={24} lg={16}>
          <Card title="最近测评" extra={<Link href={scoreHistory}>历史测评</Link>}>
            {recent.error ? (
              <RequestError error={recent.error} retry={recent.retry} />
            ) : recent.data && activeBatches === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无测评">
                {canImport && (
                  <Button type="primary" onClick={() => router.push(scoreUpload)}>
                    上传测评资料
                  </Button>
                )}
              </Empty>
            ) : (
              <Table<ApiBatch>
                rowKey="batch_id"
                loading={recent.loading}
                dataSource={recent.data?.items ?? []}
                pagination={{
                  current: offset / 5 + 1,
                  pageSize: 5,
                  total: activeBatches,
                  showSizeChanger: false,
                  hideOnSinglePage: true,
                  onChange: (page) => setOffset((page - 1) * 5),
                }}
                columns={[
                  { title: '测评名称', render: (_, item) => assessmentTitle(item) },
                  {
                    title: '操作',
                    render: (_, item) => (
                      <Link href="/insights" onClick={() => selectBatch(item)}>
                        查看分析
                      </Link>
                    ),
                  },
                ]}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Flex vertical gap={token.margin}>
            <Card title="快捷入口">
              <Space wrap>
                {canImport && (
                  <>
                    <Button icon={<UploadOutlined />} onClick={() => router.push(scoreUpload)}>
                      上传评分资料
                    </Button>
                    <Button icon={<FileZipOutlined />} onClick={() => router.push(answerUpload)}>
                      上传答题卡
                    </Button>
                  </>
                )}
                <Button
                  icon={<BarChartOutlined />}
                  disabled={!batch}
                  onClick={() => router.push('/insights')}
                >
                  学情分析
                </Button>
              </Space>
            </Card>
            <Card title="待核查" extra={<Link href={answerHistory}>历史答题卡</Link>}>
              {answerSheets.error ? (
                <RequestError error={answerSheets.error} retry={answerSheets.retry} />
              ) : !pending ? (
                <Skeleton active title={false} paragraph={{ rows: 2 }} />
              ) : pending.length ? (
                <Flex vertical gap="small">
                  {pending.slice(0, 5).map((item) => (
                    <Link key={item.assessment_id} href={answerAnalysisPath(item.assessment_id)}>
                      {item.title}
                    </Link>
                  ))}
                </Flex>
              ) : (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="最近 20 份答题卡暂无待核查项"
                />
              )}
            </Card>
          </Flex>
        </Col>
      </Row>
    </Flex>
  );
}
