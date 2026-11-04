'use client';

import { useCallback, useState } from 'react';
import { Select, Table } from 'antd';
import { useDataMode } from '../../app/data-mode';
import { Panel } from '../../components/ui';
import { itemScaleLabel } from '../../services/edu-api/assessment-items';
import type { ApiBatch, ItemStatistics } from '../../services/edu-api/types';
import { ratioDisplay } from '../../services/edu-api/view-model';
import { LoadingPanel, RequestError } from './LiveStatus';
import { ProjectAbilityRadar } from './ServerCharts';
import { useRemote } from './useRemote';

export default function AssessmentItemPage({
  batch,
  view,
}: {
  batch: ApiBatch;
  view: 'diagnosis' | 'explorer' | 'overview';
}) {
  const { client } = useDataMode();
  const [selection, setSelection] = useState<string>();
  const items = batch.recognized_items ?? [];
  const load = useCallback(
    (signal: AbortSignal) => client.itemStatistics(batch, signal),
    [client, batch],
  );
  const canReadStats = batch.available_analyses?.includes('item_statistics') && items.length > 0;
  const resource = useRemote(
    canReadStats ? `items:${batch.batch_id}:${batch.snapshot_id}` : null,
    load,
  );
  if (resource.loading) return <LoadingPanel />;
  if (resource.error) return <RequestError error={resource.error} retry={resource.retry} />;

  const statistics = new Map(resource.data?.item_statistics.map((entry) => [entry.item_id, entry]));
  const rows = items.map((item) => ({ ...item, stats: statistics.get(item.item_id) }));
  const selected = rows.find((item) => item.item_id === selection) ?? rows[0];
  const questions = items.filter((item) => item.kind === 'question').length;
  const summary =
    questions === items.length
      ? `${questions} 道题目`
      : questions === 0
        ? `${items.length} 项能力`
        : `${items.length - questions} 项能力 · ${questions} 道题目`;
  const competencies = rows.filter((item) => item.kind === 'competency');
  const scale = competencies[0]?.scale;
  const radar =
    scale &&
    scale.minimum < scale.maximum &&
    competencies.length >= 3 &&
    competencies.every(
      (item) =>
        item.scale?.minimum === scale.minimum &&
        item.scale.maximum === scale.maximum &&
        (item.scale.unit ?? '分') === (scale.unit ?? '分') &&
        item.stats?.mean.value != null,
    )
      ? competencies.map((item) => ({
          label: item.display_name,
          value: item.stats!.mean.value!,
          minimum: scale.minimum,
          maximum: scale.maximum,
        }))
      : null;

  if (!items.length) return <Panel>当前测评没有可展示的评分项目。</Panel>;
  if (view === 'explorer' && selected?.stats) {
    return (
      <div className="page-stack">
        {radar && (
          <Panel title="能力画像">
            <ProjectAbilityRadar axes={radar} />
          </Panel>
        )}
        <Panel
          title={questions === items.length ? '逐题探索' : '项目探索'}
          action={
            <Select
              aria-label={questions === items.length ? '选择题目' : '选择评分项目'}
              style={{ minWidth: 180, maxWidth: '100%' }}
              value={selected.item_id}
              options={items.map((item) => ({ value: item.item_id, label: item.display_name }))}
              showSearch={{ optionFilterProp: 'label' }}
              onChange={setSelection}
            />
          }
        >
          <h3>{selected.display_name}</h3>
          <p className="muted">
            {itemScaleLabel(selected.scale)} · {selected.stats.valid_count} 人有有效得分
          </p>
          <ItemDistribution stats={selected.stats} />
        </Panel>
      </div>
    );
  }

  return (
    <div className="page-stack">
      {view === 'diagnosis' && radar && (
        <Panel title="能力画像">
          <ProjectAbilityRadar axes={radar} />
        </Panel>
      )}
      <Panel
        title={questions === items.length ? '逐题统计' : '项目统计'}
        subtitle={`${summary} · ${batch.student_count} 名参评者`}
      >
        {!resource.data && <p className="muted">当前测评尚未开放项目统计。</p>}
        <Table
          aria-label={questions === items.length ? '逐题统计' : '评分项目统计'}
          rowKey="item_id"
          size="small"
          pagination={false}
          dataSource={rows}
          scroll={{ x: resource.data ? 720 : 360 }}
          columns={[
            { title: questions === items.length ? '题目' : '项目', dataIndex: 'display_name' },
            ...(questions > 0 && questions < items.length
              ? [
                  {
                    title: '类型',
                    render: (_: unknown, item: (typeof rows)[number]) =>
                      item.kind === 'question' ? '题目' : '能力',
                  },
                ]
              : []),
            { title: '原量表', render: (_, item) => itemScaleLabel(item.scale) },
            ...(resource.data
              ? [
                  {
                    title: '有效人数',
                    render: (_: unknown, item: (typeof rows)[number]) => item.stats?.valid_count,
                  },
                  {
                    title: '平均分',
                    render: (_: unknown, item: (typeof rows)[number]) =>
                      item.stats?.valid_count === 0
                        ? '暂无有效评分'
                        : item.stats?.mean.value == null
                          ? '—'
                          : item.stats.mean.display,
                  },
                  {
                    title: '中位数',
                    render: (_: unknown, item: (typeof rows)[number]) =>
                      item.stats?.median.value == null ? '—' : item.stats.median.display,
                  },
                  {
                    title: '实得范围',
                    render: (_: unknown, item: (typeof rows)[number]) =>
                      item.stats?.minimum.value == null || item.stats.maximum.value == null
                        ? '—'
                        : `${item.stats.minimum.display}–${item.stats.maximum.display}`,
                  },
                ]
              : []),
          ]}
        />
      </Panel>
    </div>
  );
}

function ItemDistribution({ stats }: { stats: ItemStatistics }) {
  return (
    <>
      {stats.missing_count + stats.invalid_count > 0 && (
        <p className="muted">
          缺分 {stats.missing_count} 人 · 无效评分 {stats.invalid_count} 人
        </p>
      )}
      <Table
        aria-label="得分分布"
        rowKey="score"
        size="small"
        pagination={false}
        dataSource={stats.distribution}
        locale={{ emptyText: '暂无有效得分' }}
        columns={[
          { title: '得分', dataIndex: 'score' },
          { title: '人数', dataIndex: 'count' },
          { title: '有效样本占比', render: (_, bucket) => ratioDisplay(bucket.ratio.value) },
        ]}
      />
    </>
  );
}
