'use client';
import type { ReactNode } from 'react';
import { Steps } from 'antd';

export default function UploadWorkflow({
  step,
  busy = false,
  children,
  actions,
}: {
  step: 0 | 1 | 2;
  busy?: boolean;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section
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
          classNames={{ itemWrapper: 'workflow-step-row' }}
          items={['选择文件', '检查结果', '确认导入'].map((title, index) => ({
            title,
            className: index === step ? 'workflow-step-current' : undefined,
          }))}
        />
      </aside>
      <div className="upload-workflow-main">
        <div className="upload-workflow-body">{children}</div>
        <footer className="upload-workflow-actions" hidden={!actions}>
          {actions}
        </footer>
      </div>
    </section>
  );
}
