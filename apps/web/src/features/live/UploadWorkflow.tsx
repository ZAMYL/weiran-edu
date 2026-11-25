'use client';
import type { ReactNode } from 'react';
import { Card, Steps } from 'antd';
import { useWorkspaceLayout } from '../../app/WorkspaceLayoutContext';

export default function UploadWorkflow({
  step,
  busy = false,
  steps = ['选择文件', '检查结果', '确认导入'],
  children,
  actions,
}: {
  step: 0 | 1 | 2;
  busy?: boolean;
  steps?: string[];
  children: ReactNode;
  actions?: ReactNode;
}) {
  const { uploadTypeControl } = useWorkspaceLayout();
  return (
    <Card
      title={uploadTypeControl || undefined}
      classNames={{ body: 'upload-workflow-layout' }}
      className={`panel upload-workflow upload-step-${step}`}
      aria-label="上传测评流程"
      aria-busy={busy}
      data-step={step}
    >
      <aside className="upload-workflow-header">
        <Steps
          current={step}
          orientation="vertical"
          responsive={false}
          aria-label="导入进度"
          items={steps.map((title) => ({ title }))}
        />
      </aside>
      <div className="upload-workflow-main">
        <div className="upload-workflow-body">{children}</div>
        <footer className="upload-workflow-actions" hidden={!actions}>
          {actions}
        </footer>
      </div>
    </Card>
  );
}
