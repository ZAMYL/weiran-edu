'use client';
import { Button, Input } from 'antd';

export function UploadSelection({
  filename,
  name,
  onNameChange,
  onNext,
  onClear,
  busy,
  disabled = false,
  allowDefaultName = false,
}: {
  filename: string;
  name: string;
  onNameChange: (name: string) => void;
  onNext: () => void;
  onClear: () => void;
  busy: boolean;
  disabled?: boolean;
  allowDefaultName?: boolean;
}) {
  return (
    <div className="upload-selection">
      <p>
        已选择文件：<strong>{filename}</strong>
      </p>
      <label htmlFor="selected-assessment-name">测评名称</label>
      <Input
        id="selected-assessment-name"
        value={name}
        maxLength={100}
        disabled={busy}
        status={!name.trim() && !allowDefaultName ? 'error' : undefined}
        onChange={(event) => onNameChange(event.target.value)}
      />
      <p className="muted small">默认使用文件名，可直接继续或修改名称。</p>
      {!name.trim() && (
        <p role={allowDefaultName ? 'status' : 'alert'}>
          {allowDefaultName
            ? '未填写名称时，将使用文件名。'
            : '测评名称不能为空，可保留默认文件名。'}
        </p>
      )}
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
