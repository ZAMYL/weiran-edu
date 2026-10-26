import { useState } from 'react';
import {
  ArrowRightOutlined,
  FileTextOutlined,
  LeftOutlined,
  RightOutlined,
} from '@ant-design/icons';
import {
  Alert,
  Button,
  Collapse,
  Drawer,
  Empty,
  Progress,
  Segmented,
  Select,
  Table,
  Tag,
} from 'antd';
import type { Batch, Condition, DimensionId, Student } from '../domain/model';
import { conditionLabel, filterStudents, percentage } from '../domain/statistics';
import { AssessmentContext, Panel, PendingTag, VerifiedTag } from './ui';

export type GroupPanel = 'evidence' | 'analysis' | null;
export function SourceLine({
  student,
  dimension = 'algorithm',
}: {
  student: Student;
  dimension?: DimensionId;
}) {
  const source = student.ratings[dimension].source;
  return (
    <p className="source-line">
      匿名学生 {student.id}
      <span>｜</span>
      {source.file} · {source.sheet} · 第 {source.row} 行 · {source.column} 列
    </p>
  );
}
export function CommentQuote({
  batch,
  student,
  dimension = 'algorithm',
}: {
  batch: Batch;
  student: Student;
  dimension?: DimensionId;
}) {
  return (
    <div className="comment-quote">
      <Tag variant="filled">{batch.demo ? '示例评语' : '原始评语'}</Tag>
      <blockquote>{student.ratings[dimension].comment}</blockquote>
      <SourceLine student={student} dimension={dimension} />
    </div>
  );
}
export function GroupDrawers({
  batch,
  conditions,
  panel,
  onClose,
  onPanelChange,
  onExplore,
}: {
  batch: Batch;
  conditions: Condition[];
  panel: GroupPanel;
  onClose: () => void;
  onPanelChange: (panel: GroupPanel) => void;
  onExplore: () => void;
}) {
  const students = filterStudents(batch.students, conditions);
  const conditionText = conditions.length
    ? conditions.map((c) => conditionLabel(batch, c)).join(' 且 ')
    : '本次测评全部参评者';
  const example = students.find((s) => s.id === 'S008') ?? students[0];
  return (
    <Drawer
      open={panel !== null}
      onClose={onClose}
      size={580}
      className="group-drawer"
      title={
        <div className="drawer-title">
          {panel === 'analysis' ? '测评分析' : '洞察依据'}
          {panel === 'analysis' && <Tag color="blue">规则模板 · 未接入 AI</Tag>}
        </div>
      }
      footer={
        <div className="drawer-footer">
          {panel === 'evidence' ? (
            <>
              <Button onClick={onExplore}>在画像探索中打开</Button>
              <Button
                type="primary"
                disabled={!students.length}
                onClick={() => onPanelChange('analysis')}
              >
                分析这组学生
              </Button>
            </>
          ) : (
            <>
              <span className="muted">内容需由教师核查</span>
              <Button type="primary" onClick={() => onPanelChange('evidence')}>
                查看引用依据
              </Button>
            </>
          )}
        </div>
      }
    >
      <p className="drawer-subtitle">
        {batch.name} · 当前 {students.length} 人群体
      </p>
      {panel === 'evidence' ? (
        <>
          <div className="evidence-metric">
            <div>
              <strong>{students.length}</strong> / {batch.students.length} 人{' '}
              <b>{percentage(students.length, batch.students.length)}</b>
            </div>
            <VerifiedTag />
          </div>
          <Panel title="计算条件">
            <div className="condition-box">{conditionText}</div>
            <p className="muted">统计分母：本次测评参评人数 {batch.students.length} 人</p>
            <p className="muted small">
              {batch.demo
                ? '本地合成演示记录计算一致；不代表真实学情核验。'
                : '根据当前已导入评分记录计算。'}
            </p>
          </Panel>
          <Panel title={`匹配学生记录${students.length > 3 ? '（部分）' : ''}`}>
            <Table<Student>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={students.slice(0, 3)}
              columns={[
                { title: '匿名学生', dataIndex: 'id' },
                { title: '算法评分', render: (_, s) => s.ratings.algorithm.value },
                { title: '策略评分', render: (_, s) => s.ratings.strategy.value },
                {
                  title: '来源',
                  render: (_, s) =>
                    `${s.ratings.algorithm.source.sheet} · 第${s.ratings.algorithm.source.row}行`,
                },
              ]}
            />
          </Panel>
          <Panel title="原始评语">
            {example ? (
              <CommentQuote batch={batch} student={example} />
            ) : (
              <Empty description="当前条件没有匹配记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Panel>
          <Panel
            title={
              <span>
                解释边界 <PendingTag />
              </span>
            }
          >
            <p>
              当前评分能说明表现差异，原因仍需补充题目与答题过程。原始评语是个别记录，不能替代全体学生的原因判断。
            </p>
          </Panel>
        </>
      ) : (
        <div className="analysis-sections">
          <Alert type="info" showIcon title="以下为基于当前数据的规则模板，未调用 AI 服务。" />
          <Panel
            title={
              <>
                <span className="section-number">01</span>统计事实
              </>
            }
            action={<VerifiedTag />}
          >
            <p>
              本次测评共有{' '}
              <b className="blue-text">
                {students.length} / {batch.students.length}
              </b>{' '}
              人（{percentage(students.length, batch.students.length)}）符合以下条件：
            </p>
            <div className="condition-box">{conditionText}</div>
            <Button type="link" onClick={() => onPanelChange('evidence')}>
              评分记录与筛选条件 <ArrowRightOutlined />
            </Button>
          </Panel>
          <Panel
            title={
              <>
                <span className="section-number">02</span>可能解释
              </>
            }
            action={<PendingTag />}
          >
            <p>
              这些维度的评分反映了不同方面的表现，尚不能仅依据评分判断成因。请结合评价标准和实际答题过程检查。
            </p>
          </Panel>
          <Panel
            title={
              <>
                <span className="section-number">03</span>后续核对建议
              </>
            }
          >
            <ol>
              <li>围绕当前筛选涉及的能力安排短小诊断练习。</li>
              <li>结合原始评语和评价标准，观察学生的思考与实现过程。</li>
              <li>核对群体差异，再决定后续练习或指导安排。</li>
            </ol>
          </Panel>
          <Panel
            title={
              <>
                <span className="section-number">04</span>如何验证效果
              </>
            }
          >
            <p>对照后续练习表现，再结合可比测评检查变化。</p>
            <div className="soft-note">当前未建立可比的后续测评，不展示改善幅度。</div>
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
export function StudentDrawer({
  batch,
  students,
  selectedId,
  onSelect,
  onClose,
}: {
  batch: Batch;
  students: Student[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState('五维评价');
  const [dimension, setDimension] = useState<DimensionId>('algorithm');
  const index = students.findIndex((s) => s.id === selectedId);
  const student = students[index];
  return (
    <Drawer
      open={Boolean(student)}
      onClose={onClose}
      size={520}
      title={
        <div className="student-drawer-heading">
          <span>匿名学生 {student?.id}</span>
          <div>
            <Button
              aria-label="上一名学生"
              icon={<LeftOutlined />}
              disabled={index <= 0}
              onClick={() => {
                const previous = students[index - 1];
                if (previous) onSelect(previous.id);
              }}
            />
            <Button
              aria-label="下一名学生"
              icon={<RightOutlined />}
              disabled={index < 0 || index >= students.length - 1}
              onClick={() => {
                const next = students[index + 1];
                if (next) onSelect(next.id);
              }}
            />
          </div>
        </div>
      }
      footer={
        <div className="drawer-footer">
          <span className="muted">
            当前查看{' '}
            <b className="blue-text">
              {index + 1} / {students.length}
            </b>{' '}
            名匹配学生
          </span>
          <Button onClick={onClose}>返回群体</Button>
        </div>
      }
    >
      {student && (
        <>
          <AssessmentContext title={batch.name} />
          <Segmented block value={tab} options={['五维评价', '原始记录']} onChange={setTab} />
          {tab === '五维评价' ? (
            <>
              <div className="score-bars">
                {batch.standard.dimensions.map((d) => (
                  <div key={d.id} className="score-bar">
                    <span>{d.name}</span>
                    <Progress
                      percent={(student.ratings[d.id].value / d.max) * 100}
                      showInfo={false}
                      strokeColor="#347bff"
                    />
                    <b>
                      {student.ratings[d.id].value}
                      <small> / {d.max}</small>
                    </b>
                  </div>
                ))}
              </div>
              <div className="panel-heading">
                <h2>原始评语</h2>
                <Select
                  aria-label="评语能力维度"
                  value={dimension}
                  onChange={setDimension}
                  options={batch.standard.dimensions.map((d) => ({ value: d.id, label: d.name }))}
                />
              </div>
              <CommentQuote batch={batch} student={student} dimension={dimension} />
              <Collapse
                className="standard-collapse"
                items={[
                  {
                    key: 'standard',
                    label: '评价依据',
                    children: (
                      <>
                        <p>{batch.standard.name}</p>
                        <p>标准版本：{batch.standard.version ?? '未指定'}</p>
                        <p>
                          {batch.demo
                            ? '演示标准只用于验证界面与流程；正式测评需提供实际评价标准。'
                            : '当前文件按上传时所选标准校验。'}
                        </p>
                      </>
                    ),
                  },
                ]}
              />
            </>
          ) : (
            <div className="original-records">
              {batch.standard.dimensions.map((d) => (
                <Panel
                  key={d.id}
                  title={
                    <>
                      <FileTextOutlined /> {d.name} · {student.ratings[d.id].value} 分
                    </>
                  }
                >
                  <CommentQuote batch={batch} student={student} dimension={d.id} />
                </Panel>
              ))}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
