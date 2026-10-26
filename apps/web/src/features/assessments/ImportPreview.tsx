import { useState } from 'react';
import { Alert, Button, Input, Steps, Table, Tag } from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ExclamationCircleFilled,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ImportPreview as Preview, PreviewRow } from '../../domain/model';
import { rawText } from '../../domain/import-validation';
import { Panel } from '../../components/ui';

export function ImportPreview({
  preview,
  initialName,
  onCancel,
  onConfirm,
}: {
  preview: Preview;
  initialName: string;
  onCancel: () => void;
  onConfirm: (name: string) => void;
}) {
  const [name, setName] = useState(initialName);
  const [page, setPage] = useState(1);
  const [located, setLocated] = useState<string>();
  const errors = new Set(preview.issues.flatMap((issue) => (issue.rowKey ? [issue.rowKey] : [])));
  const blocked = preview.issues.length > 0;
  function locate(key?: string) {
    if (!key) return;
    const index = preview.rows.findIndex((row) => row.key === key);
    setLocated(key);
    setPage(Math.floor(index / 10) + 1);
    setTimeout(
      () =>
        document
          .querySelector('.located-row')
          ?.scrollIntoView({ block: 'center', behavior: 'smooth' }),
      100,
    );
  }
  return (
    <div className="page-stack import-preview">
      <div className="preview-heading">
        <div>
          <h2>导入预览</h2>
          <p className="muted">
            {preview.filename} · {preview.standard.name}
          </p>
        </div>
        <Button type="link" icon={<ArrowLeftOutlined />} onClick={onCancel}>
          返回测评数据
        </Button>
      </div>
      <Panel>
        <Steps
          current={1}
          items={[{ title: '选择文件' }, { title: '自动检查' }, { title: '确认导入' }]}
        />
      </Panel>
      <div className="preview-stats">
        {[
          ['已识别学生', preview.studentCount, '人'],
          ['评价槽位', preview.studentCount * preview.standard.dimensions.length, '条'],
          ['有效评分记录', preview.validCount, '条'],
          ['待处理问题', preview.issues.length, '项'],
        ].map(([label, value, unit], i) => (
          <div
            className={`panel preview-stat ${i === 3 && blocked ? 'has-errors' : ''}`}
            key={label}
          >
            <p>{label}</p>
            <strong>{value}</strong>
            <span>{unit}</span>
          </div>
        ))}
      </div>
      <div className="import-preview-grid">
        <Panel
          title="解析预览"
          subtitle="原始评分、评语与来源保持不变；请在源文件处理问题后重新上传。"
        >
          <Table<PreviewRow>
            rowKey="key"
            size="small"
            dataSource={preview.rows}
            scroll={{ x: 700 }}
            rowClassName={(row) =>
              `${errors.has(row.key) ? 'error-row' : ''} ${located === row.key ? 'located-row' : ''}`
            }
            pagination={{
              current: page,
              onChange: setPage,
              pageSize: 10,
              showSizeChanger: false,
              showTotal: (total) => `共 ${total} 条原始记录`,
            }}
            columns={[
              { title: '匿名学生', dataIndex: 'anonymousId', width: 90 },
              { title: '能力维度', dataIndex: 'dimension', width: 140 },
              { title: '原始评分', dataIndex: 'raw', width: 90, render: rawText },
              { title: '原始评语', dataIndex: 'comment', render: (comment) => comment || '—' },
              {
                title: '来源',
                key: 'source',
                width: 150,
                render: (_, row) =>
                  `${row.source.sheet} · 第${row.source.row}行 · ${row.source.column}列`,
              },
            ]}
          />
        </Panel>
        <Panel
          title="数据质量报告"
          action={
            <Tag color={blocked ? 'error' : 'success'}>
              {blocked ? `${preview.issues.length} 项需处理` : '校验通过'}
            </Tag>
          }
          className="quality-report"
        >
          {blocked ? (
            <>
              <div className="issue-list">
                {preview.issues.map((issue) => (
                  <article className="issue-card" key={issue.key}>
                    <div>
                      <h3>
                        <ExclamationCircleFilled /> {issue.title}
                      </h3>
                      {issue.rowKey && (
                        <Button type="link" size="small" onClick={() => locate(issue.rowKey)}>
                          定位记录 →
                        </Button>
                      )}
                    </div>
                    {issue.source && (
                      <p className="issue-source">
                        {issue.source.sheet} · 第 {issue.source.row} 行 · {issue.source.column} 列
                      </p>
                    )}
                    <p>{issue.detail}</p>
                  </article>
                ))}
              </div>
              <Alert
                showIcon
                type="warning"
                title="处理全部问题后方可确认导入"
                description="原始内容不会被自动改写。"
              />
            </>
          ) : (
            <Alert
              showIcon
              type="success"
              title="所有记录通过当前标准校验"
              description="确认后将创建一个独立批次，当前浏览器刷新后不保留。"
            />
          )}
          <div className="batch-name-field">
            <label htmlFor="batch-name">测评名称</label>
            <Input
              id="batch-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              placeholder="请输入测评名称"
            />
          </div>
        </Panel>
      </div>
      <section className="panel preview-footer">
        <div>
          <Button icon={<ArrowLeftOutlined />} onClick={onCancel}>
            返回上传
          </Button>
          <Button icon={<ReloadOutlined />} onClick={onCancel}>
            重新上传
          </Button>
        </div>
        <div>
          <span className="muted">{blocked ? '请先处理校验问题' : '校验完成，等待确认'}</span>
          <Button
            type="primary"
            disabled={blocked || !name.trim()}
            icon={!blocked && <CheckCircleOutlined />}
            onClick={() => onConfirm(name.trim())}
          >
            确认导入
          </Button>
        </div>
      </section>
    </div>
  );
}
