'use client';

import type { ReactNode } from 'react';
import { App, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { themes } from './theme';
import { useDarkMode } from './theme-preference';
import { DataModeProvider } from './data-mode';

export function Providers({ children }: { children: ReactNode }) {
  const { config, styles } = themes[useDarkMode() ? 'dark' : 'light'];
  return (
    <ConfigProvider locale={zhCN} theme={config} button={{ autoInsertSpace: false }}>
      <style>{styles}</style>
      <App>
        <DataModeProvider>{children}</DataModeProvider>
      </App>
    </ConfigProvider>
  );
}
