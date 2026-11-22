import { Spin } from 'antd';

export default function Loading() {
  return (
    <div role="status" className="live-loading">
      <Spin /> 正在加载…
    </div>
  );
}
