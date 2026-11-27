import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { AntdRegistry } from '@ant-design/nextjs-registry';
import { Providers } from './providers';
import './globals.css';

export const metadata: Metadata = {
  title: '未然 EDU｜AI 时代的编程能力与学习成效诊断平台',
  description: '未然 EDU｜AI 时代的编程能力与学习成效诊断平台',
  icons: { icon: { url: '/weiran-logo.png', type: 'image/png', sizes: '192x192' } },
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <Providers>{children}</Providers>
        </AntdRegistry>
      </body>
    </html>
  );
}
