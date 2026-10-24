'use client';

import type { ReactNode } from 'react';
import { App, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { theme } from './theme';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider locale={zhCN} theme={theme} button={{ autoInsertSpace: false }}>
      <App>{children}</App>
    </ConfigProvider>
  );
}
