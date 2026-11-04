import type { ReactNode } from 'react';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  TeamOutlined,
  FileTextOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { Tag } from 'antd';
import type { Batch } from '../domain/model';

export function Panel({
  title,
  subtitle,
  action,
  className = '',
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      {(title || action) && (
        <div className="panel-heading">
          <div>
            <h2>{title}</h2>
            {subtitle && <p className="muted panel-subtitle">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function VerifiedTag() {
  return (
    <Tag className="status-tag" color="success" icon={<CheckCircleFilled />}>
      统计已核验
    </Tag>
  );
}
export function PendingTag() {
  return (
    <Tag className="status-tag" color="warning" icon={<ClockCircleOutlined />}>
      解释待验证
    </Tag>
  );
}
export function DataBadge({ demo }: { demo: boolean }) {
  return (
    <Tag className="data-badge" color={demo ? 'blue' : 'cyan'}>
      {demo ? '演示数据' : '本地导入'}
    </Tag>
  );
}
export function AssessmentContext({
  title,
  course,
  date,
}: {
  title?: string | null;
  course?: string | null;
  date?: string | null;
}) {
  return (
    <div className="assessment-context">
      <p className="drawer-subtitle">{title?.trim() || '测评'}</p>
      {course?.trim() && <p className="muted small">课程：{course.trim()}</p>}
      {date?.trim() && <p className="muted small">测评日期：{date.trim()}</p>}
    </div>
  );
}
export function StatCards({ batch }: { batch: Batch }) {
  const stats = [
    {
      title: '参评人数',
      value: batch.students.length,
      unit: '人',
      icon: <TeamOutlined />,
      color: 'blue',
    },
    {
      title: '评价记录',
      value: batch.students.length * batch.standard.dimensions.length,
      unit: '条',
      icon: <FileTextOutlined />,
      color: 'green',
    },
    {
      title: '能力维度',
      value: batch.standard.dimensions.length,
      unit: '个',
      icon: <AppstoreOutlined />,
      color: 'purple',
    },
  ];
  return (
    <div className="stat-grid">
      {stats.map((stat) => (
        <section className="panel stat-card" key={stat.title}>
          <div className={`stat-icon ${stat.color}`}>{stat.icon}</div>
          <div>
            <p className="muted">{stat.title}</p>
            <strong>{stat.value}</strong>
            <span className="stat-unit">{stat.unit}</span>
          </div>
        </section>
      ))}
    </div>
  );
}
