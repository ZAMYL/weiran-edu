'use client';
import { Button, Input } from 'antd';
import type { ReactNode } from 'react';

export function UploadSelection({
  filename,
  name,
  onNameChange,
  onNext,
  onClear,
  busy,
  disabled = false,
  allowDefaultName = false,
  children,
  showFilename = true,
}: {
  filename: string;
  name: string;
  onNameChange: (name: string) => void;
  onNext: () => void;
  onClear: () => void;
  busy: boolean;
  disabled?: boolean;
  allowDefaultName?: boolean;
  children?: ReactNode;
  showFilename?: boolean;
}) {
  return (
    <div className="upload-selection">
      {showFilename && (
        <p>
          已选择文件：<strong>{filename}</strong>
        </p>
      )}
      <label htmlFor="selected-assessment-name">测评名称</label>
      <Input
        id="selected-assessment-name"
        value={name}
        maxLength={100}
        disabled={busy}
        status={!name.trim() && !allowDefaultName ? 'error' : undefined}
        onChange={(event) => onNameChange(event.target.value)}
      />
      {!name.trim() && (
        <p role={allowDefaultName ? 'status' : 'alert'}>
          {allowDefaultName
            ? '未填写名称时，将使用文件名。'
            : '测评名称不能为空，可保留默认文件名。'}
        </p>
      )}
      {children}
      <div className="batch-actions">
        <Button
          type="primary"
          loading={busy}
          disabled={disabled || (!allowDefaultName && !name.trim())}
          onClick={onNext}
        >
          下一步：预览校验
        </Button>
        <Button disabled={busy} onClick={onClear}>
          取消选择
        </Button>
      </div>
    </div>
  );
}
