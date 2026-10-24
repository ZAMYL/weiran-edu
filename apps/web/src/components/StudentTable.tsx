import { ArrowRightOutlined } from '@ant-design/icons';
import { Button, Table } from 'antd';
import type { Batch, Student } from '../domain/model';

export function StudentTable({
  batch,
  students,
  onSelect,
  selectedId,
  compact = false,
}: {
  batch: Batch;
  students: Student[];
  onSelect: (id: string) => void;
  selectedId?: string;
  compact?: boolean;
}) {
  return (
    <Table<Student>
      size={compact ? 'small' : 'middle'}
      rowKey="id"
      dataSource={students}
      rowClassName={(student) => (student.id === selectedId ? 'selected-row' : '')}
      scroll={{ x: 670 }}
      locale={{ emptyText: '没有符合当前条件的学生，请调整或重置筛选。' }}
      pagination={{
        pageSize: 10,
        showSizeChanger: false,
        hideOnSinglePage: true,
        showTotal: (total) => `共 ${total} 人`,
      }}
      columns={[
        {
          title: '匿名学生',
          dataIndex: 'id',
          width: 96,
          render: (id) => <span className="student-id">{id}</span>,
        },
        ...batch.standard.dimensions.map((d) => ({
          title: d.name,
          key: d.id,
          align: 'center' as const,
          render: (_: unknown, s: Student) => s.ratings[d.id].value,
        })),
        {
          title: '操作',
          key: 'action',
          width: 100,
          align: 'right',
          render: (_, s) => (
            <Button
              type="link"
              size="small"
              onClick={() => onSelect(s.id)}
              aria-label={`查看 ${s.id} 详情`}
            >
              详情 <ArrowRightOutlined />
            </Button>
          ),
        },
      ]}
    />
  );
}
