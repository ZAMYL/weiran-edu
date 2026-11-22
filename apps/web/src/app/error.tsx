'use client';

import { Button, Result } from 'antd';

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main>
      <Result
        status="error"
        title={<h1>页面暂时无法加载</h1>}
        extra={
          <Button type="primary" onClick={reset}>
            重试
          </Button>
        }
      />
    </main>
  );
}
