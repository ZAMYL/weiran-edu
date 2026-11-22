'use client';
import { Disclosure } from '../components/Disclosure';
import { Button, Segmented, theme } from 'antd';
import { useState } from 'react';
import {
  BarChartOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import styles from './landing.module.css';

const tabs = [
  ['diagnosis', '学情诊断'],
  ['portrait', '画像探索'],
  ['report', '分析报告'],
] as const;
type PreviewTab = (typeof tabs)[number][0];

const assessment = '数据结构期中编程测评_计科24级';
const ratings = [
  { label: '算法与数据结构', value: 3.64, axis: [160, 15], anchor: 'middle' },
  { label: '代码实现能力', value: 3.82, axis: [234, 79], anchor: 'start' },
  { label: '问题建模能力', value: 3.41, axis: [204, 174], anchor: 'start' },
  { label: '效率意识', value: 2.89, axis: [116, 174], anchor: 'end' },
  { label: '解题策略', value: 3.75, axis: [86, 79], anchor: 'end' },
] as const;
const radarOutline = [
  [160, 34],
  [226, 82],
  [200, 160],
  [120, 160],
  [94, 82],
] as const;
const radarValues = [
  [160, 52],
  [208, 87],
  [188, 142],
  [137, 136],
  [110, 86],
] as const;
const scatter = [
  {
    score: 1.5,
    efficiency: 1.5,
    count: 6,
    x: 67,
    y: 112,
    size: 10,
    color: 'colorPrimaryBorder',
  },
  {
    score: 2,
    efficiency: 2,
    count: 9,
    x: 101,
    y: 98,
    size: 12,
    color: 'colorPrimaryBorderHover',
  },
  {
    score: 2.5,
    efficiency: 2.5,
    count: 14,
    x: 135,
    y: 81,
    size: 14,
    color: 'colorPrimaryHover',
  },
  {
    score: 3,
    efficiency: 2.5,
    count: 12,
    x: 171,
    y: 82,
    size: 13,
    color: 'colorPrimaryHover',
  },
  { score: 3.5, efficiency: 3, count: 19, x: 206, y: 65, size: 16, color: 'colorPrimary' },
  { score: 4, efficiency: 2.5, count: 15, x: 242, y: 81, size: 15, color: 'colorPrimary' },
  { score: 4, efficiency: 3.5, count: 18, x: 241, y: 50, size: 16, color: 'colorPrimary' },
  {
    score: 4.5,
    efficiency: 4,
    count: 21,
    x: 276,
    y: 35,
    size: 17,
    color: 'colorPrimaryActive',
  },
  {
    score: 5,
    efficiency: 4.5,
    count: 14,
    x: 313,
    y: 20,
    size: 14,
    color: 'colorPrimaryHover',
  },
] as const;
type ScatterPoint = (typeof scatter)[number];

function Radar() {
  const { token: tokens } = theme.useToken();
  return (
    <svg className={styles.radar} viewBox="0 0 320 200" role="img" aria-label="示例班级五维平均分">
      <g stroke={tokens.colorBorder} strokeWidth=".8" fill="none">
        {[1, 0.75, 0.5, 0.25].map((scale) => (
          <polygon
            key={scale}
            points={radarOutline
              .map(([x, y]) => `${160 + (x - 160) * scale},${102 + (y - 102) * scale}`)
              .join(' ')}
          />
        ))}
        {radarOutline.map(([x, y]) => (
          <line key={x} x1="160" y1="102" x2={x} y2={y} />
        ))}
      </g>
      <polygon
        points={radarValues.map((point) => point.join(',')).join(' ')}
        fill={tokens.colorPrimaryBgHover}
        stroke={tokens.colorPrimary}
        strokeWidth="2"
      />
      {radarValues.map(([x, y]) => (
        <circle key={x} cx={x} cy={y} r="3" fill={tokens.colorPrimary} />
      ))}
      {ratings.map(({ label, value, axis: [x, y], anchor }) => (
        <text
          key={label}
          x={x}
          y={y}
          textAnchor={anchor}
          fill={tokens.colorTextSecondary}
          fontSize="10"
        >
          {label}
          <tspan x={x} dy="13" fill={tokens.colorPrimary} fontWeight="600">
            {value}
          </tspan>
        </text>
      ))}
    </svg>
  );
}

function Scatter({
  selected,
  onSelect,
}: {
  selected: ScatterPoint | null;
  onSelect: (point: ScatterPoint) => void;
}) {
  const { token: tokens } = theme.useToken();
  return (
    <svg
      className={styles.scatter}
      viewBox="0 0 340 160"
      role="group"
      aria-label="代码实现能力与效率意识示例矩阵"
    >
      {[1, 2, 3, 4, 5].map((tick) => {
        const x = 36 + (tick - 1) * 71;
        const y = 134 - (tick - 1) * 30;
        return (
          <g key={tick}>
            <path
              d={`M36 ${y}H320 M${x} 14V134`}
              stroke={tick === 1 ? tokens.colorTextTertiary : tokens.colorBorderSecondary}
              strokeWidth=".8"
            />
            <text x="29" y={y + 3} textAnchor="end" fontSize="9" fill={tokens.colorTextSecondary}>
              {tick}
            </text>
            <text x={x} y="148" textAnchor="middle" fontSize="9" fill={tokens.colorTextSecondary}>
              {tick}
            </text>
          </g>
        );
      })}
      {scatter.map((point) => (
        <rect
          key={point.x}
          x={point.x}
          y={point.y}
          width={point.size}
          height={point.size}
          rx="2"
          fill={tokens[point.color]}
          stroke={selected === point ? tokens.colorText : undefined}
          tabIndex={0}
          role="button"
          aria-pressed={selected === point}
          aria-label={`代码实现 ${point.score}，效率意识 ${point.efficiency}，${point.count} 人`}
          onClick={() => onSelect(point)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              onSelect(point);
            }
          }}
        />
      ))}
    </svg>
  );
}

function PreviewMetrics({ metrics }: { metrics: ReadonlyArray<readonly [string, string]> }) {
  return (
    <dl className={styles.previewMetrics}>
      {metrics.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function LandingPreview({ onEnter, ready }: { onEnter: () => void; ready: boolean }) {
  const [tab, setTab] = useState<PreviewTab>('diagnosis');
  const [chart, setChart] = useState<'radar' | 'bar'>('radar');
  const [evidenceOpen, setEvidenceOpen] = useState(true);
  const [focused, setFocused] = useState(false);
  const [selected, setSelected] = useState<ScatterPoint | null>(null);
  const [included, setIncluded] = useState(true);

  return (
    <section className={styles.preview} aria-label="教师工作台预览（示例数据）">
      <div className={styles.windowBar}>
        <span className={styles.windowDots} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className={styles.windowTitle}>
          <CheckCircleOutlined aria-hidden="true" /> 未然 EDU · 教师诊断工作台示例
        </span>
        <span>交互</span>
      </div>
      <div className={styles.previewCanvas}>
        <div className={styles.previewNav}>
          <strong className={styles.brand}>未然 EDU</strong>
          <Segmented
            className={styles.previewSegmented}
            size="small"
            aria-label="预览页面"
            value={tab}
            onChange={setTab}
            options={tabs.map(([value, label]) => ({ value, label }))}
          />
          <span className={styles.previewLabel}>教师工作台</span>
        </div>
        <div
          className={styles.previewViewport}
          key={tab}
          aria-label={`${tabs.find(([id]) => id === tab)?.[1]}示例`}
        >
          {tab === 'diagnosis' && (
            <>
              <div className={`${styles.previewCard} ${styles.previewOverview}`}>
                <div className={styles.previewHeading}>
                  <strong>学情诊断</strong>
                  <span className={styles.assessment}>
                    {assessment} <span aria-hidden="true">⌄</span>
                  </span>
                </div>
                <div className={styles.previewTotals}>
                  <span>
                    <TeamOutlined aria-hidden="true" />
                    <span>
                      参评人数 <strong>128 人</strong>
                    </span>
                  </span>
                  <span>
                    <FileTextOutlined aria-hidden="true" />
                    <span>
                      有效记录 <strong>640 条</strong>
                    </span>
                  </span>
                  <span>
                    <BarChartOutlined aria-hidden="true" />
                    <span>
                      能力维度 <strong>5 个</strong>
                    </span>
                  </span>
                </div>
              </div>
              <div className={styles.previewColumns}>
                <section className={styles.previewCard}>
                  <div className={styles.previewCardHeading}>
                    <div>
                      <h3>班级能力画像</h3>
                      <p>全体参评学生 · 维度平均得分</p>
                    </div>
                    <Segmented
                      className={styles.previewSegmented}
                      size="small"
                      aria-label="预览评分图表"
                      value={chart}
                      onChange={setChart}
                      options={[
                        { value: 'radar', label: '能力图谱' },
                        { value: 'bar', label: '分项对比' },
                      ]}
                    />
                  </div>
                  {chart === 'radar' ? (
                    <Radar />
                  ) : (
                    <div className={styles.previewBars}>
                      {[...ratings]
                        .sort((a, b) => b.value - a.value)
                        .map(({ label, value }) => (
                          <div key={label}>
                            <p>
                              <span>{label}</span>
                              <strong>{value} / 5.0</strong>
                            </p>
                            <div className={styles.barTrack}>
                              <span style={{ width: `${value * 20}%` }} />
                            </div>
                          </div>
                        ))}
                    </div>
                  )}
                  <Button
                    type="text"
                    className={styles.previewLink}
                    htmlType="button"
                    onClick={() => setTab('portrait')}
                  >
                    进入画像探索 →
                  </Button>
                </section>
                <section className={styles.previewCard}>
                  <div className={styles.previewCardHeading}>
                    <h3>学情洞察</h3>
                    <div className={styles.segmented}>
                      <strong>核心发现</strong>
                      <span>分布概览</span>
                    </div>
                  </div>
                  <h4 className={styles.findingTitle}>
                    代码实现与效率意识分化明显：高分实现者中近半数时间复杂度仍待优化
                  </h4>
                  <PreviewMetrics
                    metrics={[
                      ['维度关联系数', '0.84'],
                      ['有效配对样本', '128 人'],
                      ['待强化群体', '46 人'],
                    ]}
                  />
                  <Disclosure
                    className={styles.previewEvidence}
                    expanded={evidenceOpen}
                    onExpandedChange={setEvidenceOpen}
                    label={<>如何理解这条发现</>}
                  >
                    <p>
                      在代码实现得分 ≥ 4.0 的 54 名学生中，有 25 人效率意识低于
                      3.0，主要表现为嵌套循环冗余，建议结合复杂度分析开展针对性讲评。
                    </p>
                  </Disclosure>
                  <div className={styles.previewActions}>
                    <Button
                      type="primary"
                      className={styles.previewPrimary}
                      htmlType="button"
                      onClick={() => setTab('report')}
                    >
                      查看具体评分
                    </Button>
                    <Button
                      type="text"
                      htmlType="button"
                      onClick={() => {
                        setFocused(true);
                        setTab('portrait');
                      }}
                    >
                      看看这组学生
                    </Button>
                  </div>
                </section>
              </div>
            </>
          )}
          {tab === 'portrait' && (
            <>
              <div className={`${styles.previewCard} ${styles.previewOverview}`}>
                <div className={styles.previewHeading}>
                  <strong>画像探索</strong>
                  <span className={styles.assessment}>{assessment}</span>
                </div>
                <span>
                  总计 <strong>128</strong> 人
                </span>
                <div className={styles.segmented}>
                  <strong>群体分布</strong>
                  <span>个体明细</span>
                </div>
              </div>
              <div className={`${styles.previewCard} ${styles.previewOverview}`}>
                <div className={styles.filters}>
                  <Button type="text" htmlType="button" onClick={() => setFocused(false)}>
                    筛选条件
                  </Button>
                  <Button
                    type="primary"
                    className={styles.previewPrimary}
                    htmlType="button"
                    onClick={() => setFocused(true)}
                  >
                    定位薄弱群
                  </Button>
                  <Button
                    type="text"
                    htmlType="button"
                    onClick={() => {
                      setFocused(false);
                      setSelected(null);
                    }}
                  >
                    重置
                  </Button>
                </div>
                <span aria-live="polite">
                  当前筛选 <strong>{focused ? '46' : '128'}</strong> / 128 人 · 占比{' '}
                  <strong>{focused ? '35.9%' : '100%'}</strong>
                </span>
              </div>
              <div className={`${styles.previewColumns} ${styles.portraitColumns}`}>
                <section className={styles.previewCard}>
                  <div className={styles.previewCardHeading}>
                    <h3>双维能力分布矩阵</h3>
                    <span>代码实现能力 × 效率意识</span>
                  </div>
                  <p className={styles.scatterTip} aria-live="polite">
                    {selected
                      ? `已选中区间（代码实现 ${selected.score.toFixed(1)}，效率意识 ${selected.efficiency.toFixed(1)}）：共 ${selected.count} 名学生`
                      : '点击矩阵中的分布区块，可核验对应得分区间的学生人数。'}
                  </p>
                  <Scatter selected={selected} onSelect={setSelected} />
                </section>
                <section className={styles.previewCard}>
                  <h3>群体维度均值</h3>
                  <ul className={styles.ratings}>
                    {ratings.map(({ label, value }) => (
                      <li key={label}>
                        <span>{label}</span>
                        <strong>{value}</strong>
                        <span>128 人</span>
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
            </>
          )}
          {tab === 'report' && (
            <div className={`${styles.previewColumns} ${styles.reportColumns}`}>
              <section className={styles.previewCard}>
                <h3>报告摘要</h3>
                <h4 className={styles.findingTitle}>计科24级期中编程能力诊断报告</h4>
                <dl className={styles.reportSummary}>
                  <div>
                    <dt>评估对象</dt>
                    <dd>全部参评学生</dd>
                  </div>
                  <div>
                    <dt>样本规模</dt>
                    <dd>128 / 128 人</dd>
                  </div>
                  <div>
                    <dt>数据完整度</dt>
                    <dd>100%</dd>
                  </div>
                  <div>
                    <dt>纳入报告</dt>
                    <dd>{included ? 12 : 11}</dd>
                  </div>
                  <div>
                    <dt>暂不纳入</dt>
                    <dd>{included ? 0 : 1}</dd>
                  </div>
                </dl>
                <Button
                  type="primary"
                  className={styles.previewPrimary}
                  htmlType="button"
                  disabled={!ready}
                  onClick={onEnter}
                >
                  导出诊断报告
                </Button>
              </section>
              <section className={styles.previewCard}>
                <h3>结论核验清单</h3>
                <div className={styles.reportFinding}>
                  <div>
                    <strong>01　效率意识整体偏低，成为制约高分段突破的主要短板</strong>
                    <span>{included ? '纳入报告' : '暂不纳入'}</span>
                  </div>
                  <p>
                    统计样本 128 人 · 效率意识均分 2.89，显著低于代码实现均分
                    3.82，建议补充复杂度优化专题训练。
                  </p>
                  <div className={styles.previewActions} role="group" aria-label="示例结论纳入状态">
                    <Button
                      type="text"
                      htmlType="button"
                      aria-pressed={included}
                      onClick={() => setIncluded(true)}
                    >
                      纳入报告
                    </Button>
                    <Button
                      type="text"
                      htmlType="button"
                      aria-pressed={!included}
                      onClick={() => setIncluded(false)}
                    >
                      暂不纳入
                    </Button>
                  </div>
                </div>
                <div className={styles.reportFinding}>
                  <div>
                    <strong>02　问题建模与数据结构选型呈现高度正相关（r = 0.88）</strong>
                    <span>纳入报告</span>
                  </div>
                </div>
                <div className={styles.reportFinding}>
                  <div>
                    <strong>03　分层教学建议：针对 46 名基础薄弱学生设置阶梯实验任务</strong>
                    <span>纳入报告</span>
                  </div>
                </div>
              </section>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
