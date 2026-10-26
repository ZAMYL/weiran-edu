'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';
import {
  ArrowRightOutlined,
  BulbOutlined,
  CommentOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Button, Empty, Segmented, Tag } from 'antd';
import { useBatch, useWorkspace } from '../../app/store';
import { DEFAULT_CONDITIONS } from '../../domain/model';
import { conditionLabel, filterStudents, percentage } from '../../domain/statistics';
import { Panel, PendingTag, StatCards, VerifiedTag } from '../../components/ui';
import { AbilityChart } from '../../components/AbilityChart';
import {
  CommentQuote,
  GroupDrawers,
  StudentDrawer,
  type GroupPanel,
} from '../../components/GroupDrawers';

export default function InsightsPage() {
  const batch = useBatch();
  const setConditions = useWorkspace((s) => s.setConditions);
  const { fullscreen } = useWorkspaceLayout();
  const router = useRouter();
  const [chartMode, setChartMode] = useState<'radar' | 'distribution'>('radar');
  const [panel, setPanel] = useState<GroupPanel>(null);
  const [studentId, setStudentId] = useState<string | null>(null);
  const [topic, setTopic] = useState('算法选择');
  const students = filterStudents(batch.students, DEFAULT_CONDITIONS);
  const commentRecords = students.flatMap((student) =>
    batch.standard.dimensions
      .filter((d) => student.ratings[d.id].comment.includes(topic))
      .map((d) => ({ student, dimension: d.id })),
  );
  const example =
    commentRecords.find((record) => record.student.id === 'S008') ?? commentRecords[0];
  const openExplorer = () => {
    setConditions(DEFAULT_CONDITIONS);
    router.push('/explorer');
  };
  return (
    <div className="insights-page page-stack">
      <StatCards batch={batch} />
      <div className="insights-grid">
        <div className="insights-left">
          <Panel
            className="radar-panel"
            title="本次测评概况"
            subtitle={`测评平均分 · ${batch.standard.name}`}
            action={
              <Segmented
                value={chartMode}
                onChange={(value) => setChartMode(value as 'radar' | 'distribution')}
                options={[
                  { label: '能力画像', value: 'radar' },
                  { label: '评分分布', value: 'distribution' },
                ]}
              />
            }
          >
            <AbilityChart batch={batch} mode={chartMode} />
            <div className="chart-caption">
              <span>
                <i className="legend-dot" />
                本次测评 · {batch.students.length} 人参评
              </span>
              <span>基于当前评分记录计算</span>
            </div>
            <div className="panel-bottom-link">
              <Button type="link" onClick={openExplorer}>
                进入画像探索 <ArrowRightOutlined />
              </Button>
            </div>
          </Panel>
          {fullscreen && (
            <Panel
              title={
                <>
                  评价线索 <small>来自当前群体的原始评语</small>
                </>
              }
              className="clues-panel"
            >
              {students.slice(0, 2).map((s) => (
                <div className="clue-row" key={s.id}>
                  <CommentOutlined />
                  <span>{s.ratings.coding.comment}</span>
                  <Button type="link" onClick={() => setStudentId(s.id)}>
                    查看原文
                  </Button>
                </div>
              ))}
            </Panel>
          )}
        </div>
        <div className="insights-right">
          <section className="finding-card">
            <div className="finding-top">
              <span>
                <BulbOutlined /> <Tag>重点发现</Tag>
              </span>
              <VerifiedTag />
            </div>
            <h2>策略评分较高，算法评分偏低的群体</h2>
            <div className="finding-conditions">
              {DEFAULT_CONDITIONS.map((c) => (
                <span key={c.dimension}>{conditionLabel(batch, c)}</span>
              ))}
            </div>
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
            <p>{students.length} 名学生同时符合上述两项评分条件。</p>
            <div className="finding-actions">
              <Button onClick={() => setPanel('evidence')}>查看 {students.length} 人依据</Button>
              <Button type="text" onClick={openExplorer}>
                进入画像探索 <ArrowRightOutlined />
              </Button>
            </div>
          </section>
          <Panel
            title="评语主题与原始依据"
            subtitle="从原始评语中检索线索，结合记录理解群体表现。"
            action={
              <Button type="link" onClick={() => setPanel('evidence')}>
                查看依据 <ArrowRightOutlined />
              </Button>
            }
            className="comments-panel"
          >
            <div className="topic-tabs" role="group" aria-label="评语主题">
              {['算法选择', '边界处理', '调试方法'].map((t) => (
                <button className={topic === t ? 'active' : ''} key={t} onClick={() => setTopic(t)}>
                  {t}
                </button>
              ))}
            </div>
            {example ? (
              <>
                <CommentQuote
                  batch={batch}
                  student={example.student}
                  dimension={example.dimension}
                />
                <div className="quote-action">
                  <Button type="link" onClick={() => setStudentId(example.student.id)}>
                    查看原始记录 <ArrowRightOutlined />
                  </Button>
                </div>
              </>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={`当前群体没有含“${topic}”的评语`}
              />
            )}
          </Panel>
        </div>
      </div>
      <section className="panel teaching-strip">
        <div>
          <h2>
            下一步：核对群体差异 <PendingTag />
          </h2>
          <p className="muted">能力差异的原因仍需结合题目与答题过程验证。</p>
        </div>
        <div className="teaching-context">
          分析对象：当前 <b>{students.length}</b> 人群体
        </div>
        <Button
          size="large"
          type="primary"
          icon={<ThunderboltOutlined />}
          disabled={!students.length}
          onClick={() => setPanel('analysis')}
        >
          生成测评分析
        </Button>
      </section>
      <GroupDrawers
        key={batch.id}
        batch={batch}
        conditions={DEFAULT_CONDITIONS}
        panel={panel}
        onClose={() => setPanel(null)}
        onPanelChange={setPanel}
        onExplore={openExplorer}
      />
      <StudentDrawer
        key={`student-${batch.id}`}
        batch={batch}
        students={students}
        selectedId={studentId}
        onSelect={setStudentId}
        onClose={() => setStudentId(null)}
      />
    </div>
  );
}
