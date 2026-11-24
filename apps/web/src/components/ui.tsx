import { tokens } from '../app/theme';
import type { ReactNode } from 'react';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  TeamOutlined,
  FileTextOutlined,
  AppstoreOutlined,
} from '@ant-design/icons';
import { Card, Col, Descriptions, Flex, Row, Statistic, Tag, Typography } from 'antd';
import type { Batch } from '../domain/model';

export function Panel({
  title,
  subtitle,
  action,
  className = '',
  size = 'medium',
  variant = 'outlined',
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  className?: string;
  size?: 'small' | 'medium';
  variant?: 'outlined' | 'borderless';
  children: ReactNode;
}) {
  return (
    <Card
      size={size}
      variant={variant}
      className={className ? `panel ${className}` : 'panel'}
      classNames={{ header: 'panel-heading' }}
      title={
        title ? (
          <Flex vertical gap={tokens.sizeXXS / 2} className="panel-title-wrap">
            <Typography.Title className="panel-heading-title" level={2}>
              {title}
            </Typography.Title>
            {subtitle && (
              <Typography.Text className="panel-subtitle" type="secondary">
                {subtitle}
              </Typography.Text>
            )}
          </Flex>
        ) : undefined
      }
      extra={action}
    >
      {children}
    </Card>
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
    <Descriptions
      className="assessment-context"
      size="small"
      column={1}
      items={[
        { key: 'title', label: '测评', children: title?.trim() || '测评' },
        ...(course?.trim() ? [{ key: 'course', label: '课程', children: course.trim() }] : []),
        ...(date?.trim() ? [{ key: 'date', label: '测评日期', children: date.trim() }] : []),
      ]}
    />
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
    <Row gutter={[tokens.size, tokens.size]} className="stat-grid">
      {stats.map((stat) => (
        <Col xs={24} sm={8} key={stat.title}>
          <Card className="panel stat-card">
            <Flex align="center" gap={tokens.size}>
              <div className={`stat-icon ${stat.color}`}>{stat.icon}</div>
              <Statistic title={stat.title} value={stat.value} suffix={stat.unit} />
            </Flex>
          </Card>
        </Col>
      ))}
    </Row>
  );
}
