'use client';

import { useState } from 'react';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';
import { App, Button, Empty, InputNumber, Modal, Select, Tag } from 'antd';
import { ArrowRightOutlined, TeamOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useBatch, useWorkspace } from '../../app/store';
import {
  DEFAULT_CONDITIONS,
  type Condition,
  type DimensionId,
  type Operator,
} from '../../domain/model';
import {
  conditionLabel,
  distribution,
  filterStudents,
  heatmap,
  percentage,
  scaleValues,
} from '../../domain/statistics';
import { Panel, VerifiedTag } from '../../components/ui';
import { StudentTable } from '../../components/StudentTable';
import { GroupDrawers, StudentDrawer, type GroupPanel } from '../../components/GroupDrawers';

export default function ExplorerPage() {
  const batch = useBatch();
  const conditions = useWorkspace((s) => s.conditions);
  const setConditions = useWorkspace((s) => s.setConditions);
  return (
    <ExplorerContent
      key={batch.id}
      batch={batch}
      conditions={conditions}
      setConditions={setConditions}
    />
  );
}
function ExplorerContent({
  batch,
  conditions,
  setConditions,
}: {
  batch: ReturnType<typeof useBatch>;
  conditions: Condition[];
  setConditions: (conditions: Condition[]) => void;
}) {
  const { fullscreen } = useWorkspaceLayout();
  const [draft, setDraft] = useState<Condition[]>(
    conditions.length ? conditions.map((c) => ({ ...c })) : [],
  );
  const [panel, setPanel] = useState<GroupPanel>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [axes, setAxes] = useState<{ x: DimensionId; y: DimensionId }>({
    x: 'algorithm',
    y: 'strategy',
  });
  const { message } = App.useApp();
  const students = filterStudents(batch.students, conditions);
  const matrix = heatmap(batch, axes.x, axes.y);
  const maxCount = Math.max(1, ...matrix.cells.flat());
  const xName = batch.standard.dimensions.find((d) => d.id === axes.x)!.name;
  const yName = batch.standard.dimensions.find((d) => d.id === axes.y)!.name;
  const updateDraft = (index: number, patch: Partial<Condition>) =>
    setDraft(draft.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const reset = () => {
    setDraft([]);
    setConditions([]);
    setSelectedId(null);
  };
  const selectCell = (x: number, y: number) => {
    const next: Condition[] = [
      { dimension: axes.x, operator: '=', value: x },
      { dimension: axes.y, operator: '=', value: y },
    ];
    setDraft(next);
    setConditions(next);
    setSelectedId(null);
  };
  function apply() {
    if (draft.some((c) => !Number.isFinite(c.value))) {
      message.error('请填写有效的筛选分数。');
      return;
    }
    setConditions(draft);
    setSelectedId(null);
  }
  return (
    <div className="explorer-page page-stack">
      <Panel className="filter-panel">
        <div className="filter-builder">
          {draft.map((c, index) => (
            <div className="filter-condition" key={index}>
              {index > 0 && <span className="and-token">且</span>}
              <Select
                aria-label={`条件${index + 1}能力维度`}
                className="dimension-select"
                value={c.dimension}
                onChange={(dimension) => updateDraft(index, { dimension })}
                options={batch.standard.dimensions.map((d) => ({ value: d.id, label: d.name }))}
              />
              <Select
                aria-label={`条件${index + 1}比较方式`}
                className="operator-select"
                value={c.operator}
                onChange={(operator) => updateDraft(index, { operator: operator as Operator })}
                options={['>=', '<', '=', '<=', '>'].map((v) => ({
                  value: v,
                  label: v.replace('>=', '≥').replace('<=', '≤'),
                }))}
              />
              <InputNumber
                aria-label={`条件${index + 1}评分`}
                value={c.value}
                onChange={(value) => updateDraft(index, { value: value ?? NaN })}
              />
            </div>
          ))}
          {draft.length < 2 && (
            <Button
              onClick={() => {
                const condition = DEFAULT_CONDITIONS[draft.length];
                if (condition) setDraft([...draft, { ...condition }]);
              }}
            >
              ＋ 添加条件
            </Button>
          )}
          <Button type="primary" onClick={apply}>
            应用筛选
          </Button>
          <Button className="soft-button" onClick={reset}>
            重置
          </Button>
          <Button type="link" className="filter-rules" onClick={() => setRulesOpen(true)}>
            查看筛选规则 <ArrowRightOutlined />
          </Button>
        </div>
      </Panel>
      <div className="match-summary">
        <span>
          当前匹配 <strong>{students.length}</strong> 人 · 占参评人数{' '}
          <b>{percentage(students.length, batch.students.length)}</b>
        </span>
        <span className="summary-divider" />
        {conditions.length ? (
          conditions.map((c, i) => (
            <Tag
              closable
              key={`${c.dimension}-${i}`}
              onClose={() => {
                const next = conditions.filter((_, index) => index !== i);
                setConditions(next);
                setDraft(next);
              }}
            >
              {conditionLabel(batch, c)}
            </Tag>
          ))
        ) : (
          <Tag>未设置条件 · 全部参评者</Tag>
        )}
      </div>
      <div className={`explorer-grid ${fullscreen ? 'explorer-fullscreen-grid' : ''}`}>
        <Panel
          title="能力组合分布"
          subtitle={`横轴为“${xName}”，纵轴为“${yName}”`}
          className="heatmap-panel"
        >
          <div className="axis-controls">
            <Select
              aria-label="热力图横轴"
              value={axes.x}
              onChange={(x) => setAxes({ x, y: x === axes.y ? axes.x : axes.y })}
              options={batch.standard.dimensions.map((d) => ({
                value: d.id,
                label: `横轴 · ${d.name}`,
              }))}
            />
            <Select
              aria-label="热力图纵轴"
              value={axes.y}
              onChange={(y) => setAxes({ ...axes, y })}
              options={batch.standard.dimensions
                .filter((d) => d.id !== axes.x)
                .map((d) => ({ value: d.id, label: `纵轴 · ${d.name}` }))}
            />
          </div>
          <div className="heatmap-layout">
            <div className="heatmap-y-title">
              {yName}
              <small>评分</small>
            </div>
            <div
              className="heatmap-matrix"
              style={{ gridTemplateColumns: `28px repeat(${matrix.xs.length}, 1fr)` }}
            >
              {matrix.ys.map((y, yi) => (
                <div className="matrix-row" key={y}>
                  <span className="axis-label">{y}</span>
                  {matrix.xs.map((x, xi) => {
                    const count = matrix.cells[yi]?.[xi] ?? 0;
                    const axisConditions = conditions.filter(
                      (c) => c.dimension === axes.x || c.dimension === axes.y,
                    );
                    const isSelected =
                      axisConditions.length > 0 &&
                      axisConditions.every((c) => {
                        const v = c.dimension === axes.x ? x : y;
                        return c.operator === '>='
                          ? v >= c.value
                          : c.operator === '<'
                            ? v < c.value
                            : c.operator === '<='
                              ? v <= c.value
                              : c.operator === '>'
                                ? v > c.value
                                : v === c.value;
                      });
                    return (
                      <button
                        key={x}
                        className={`heat-cell ${isSelected ? 'selected' : ''}`}
                        style={{
                          background: count
                            ? `rgba(36, 99, 245, ${0.12 + (0.83 * count) / maxCount})`
                            : '#f7faff',
                          color: count / maxCount > 0.55 ? '#fff' : '#21365f',
                        }}
                        onClick={() => selectCell(x, y)}
                        aria-label={`${xName}${x}分，${yName}${y}分，${count}人，点击筛选`}
                      >
                        {count}
                      </button>
                    );
                  })}
                </div>
              ))}
              <span />
              {matrix.xs.map((x) => (
                <span key={x} className="axis-label bottom-axis">
                  {x}
                </span>
              ))}
            </div>
          </div>
          <div className="heatmap-x-title">{xName}评分</div>
          <div className="heatmap-legend">
            <span>参评人数</span>
            <span className="gradient-legend" />
            <span>0 — {maxCount}</span>
          </div>
          <p className="muted small heatmap-help">
            蓝框为当前轴向条件；点击单元格可筛选该评分组合。
          </p>
        </Panel>
        {fullscreen ? (
          <Panel
            title={
              <>
                匹配学生列表 <small>共 {students.length} 人</small>
              </>
            }
            className="fullscreen-table"
          >
            <StudentTable
              batch={batch}
              students={students}
              onSelect={setSelectedId}
              selectedId={selectedId ?? undefined}
              compact
            />
          </Panel>
        ) : (
          <section className="finding-card group-summary-card">
            <div className="finding-top">
              <span>
                <TeamOutlined /> <Tag>当前群体</Tag>
              </span>
              <VerifiedTag />
            </div>
            <h2>{conditions.length ? '符合当前能力组合条件的学生' : '本次测评全部参评者'}</h2>
            <div className="finding-numbers">
              <strong>
                {students.length}
                <small> / {batch.students.length} 人</small>
              </strong>
              <span className="number-divider" />
              <strong>
                {percentage(students.length, batch.students.length)}
                <em>占参评人数</em>
              </strong>
            </div>
            <div className="group-condition-text">
              {conditions.map((c) => conditionLabel(batch, c)).join(' 且 ') || '未设置筛选条件'}
            </div>
            <div className="group-evidence-link">
              <Button onClick={() => setPanel('evidence')}>
                查看依据 <ArrowRightOutlined />
              </Button>
            </div>
            <div className="group-distribution">
              <h3>群体评分分布</h3>
              <p>各维度评分占当前群体的比例</p>
              <div className="distribution-legend">
                {[1, 2, 3, 4, 5].map((n, i) => (
                  <span key={n}>
                    <i
                      style={{
                        background: ['#e6efff', '#bcd5ff', '#87b3ff', '#4e8cff', '#2463f5'][i],
                      }}
                    />
                    {n}
                  </span>
                ))}
              </div>
              {batch.standard.dimensions.map((d) => (
                <div className="distribution-row" key={d.id}>
                  <span>{d.name}</span>
                  <div>
                    {distribution(students, d.id, scaleValues(d.min, d.max, d.step)).map(
                      (count, i) => (
                        <span
                          key={i}
                          title={`${i + d.min} 分：${count} 人`}
                          style={{
                            width: percentage(count, students.length),
                            background: ['#e6efff', '#bcd5ff', '#87b3ff', '#4e8cff', '#2463f5'][i],
                          }}
                        />
                      ),
                    )}
                  </div>
                </div>
              ))}
              {!students.length && (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无匹配记录" />
              )}
            </div>
          </section>
        )}
      </div>
      {!fullscreen && (
        <Panel
          title={
            <>
              匹配学生 <small>共 {students.length} 人</small>
            </>
          }
          className="students-panel"
        >
          <StudentTable
            batch={batch}
            students={students}
            onSelect={setSelectedId}
            selectedId={selectedId ?? undefined}
          />
        </Panel>
      )}
      <section className="panel teaching-strip">
        <div>
          <h2>
            当前分析对象：<span className="blue-text">{students.length}</span> 人群体
          </h2>
          <p className="muted">基于已应用的筛选条件，进一步检查群体表现。</p>
        </div>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          disabled={!students.length}
          onClick={() => setPanel('analysis')}
        >
          分析这组学生
        </Button>
      </section>
      <StudentDrawer
        batch={batch}
        students={students}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onClose={() => setSelectedId(null)}
      />
      <GroupDrawers
        batch={batch}
        conditions={conditions}
        panel={panel}
        onClose={() => setPanel(null)}
        onPanelChange={setPanel}
        onExplore={() => setPanel(null)}
      />
      <Modal
        open={rulesOpen}
        onCancel={() => setRulesOpen(false)}
        title="筛选规则"
        footer={
          <Button type="primary" onClick={() => setRulesOpen(false)}>
            知道了
          </Button>
        }
      >
        <p>多个条件按“且”组合。编辑条件后点击“应用筛选”，人数、群体评分分布和列表一同更新。</p>
        <p>
          占比的分母始终是本次测评参评人数。热力图显示本次测评总体分布，点击某个格子会按该评分组合精确筛选。
        </p>
        <p>重置后显示全部参评者。切换测评恢复默认探索条件。</p>
      </Modal>
    </div>
  );
}
