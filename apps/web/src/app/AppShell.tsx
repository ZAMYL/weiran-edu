'use client';

import { useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { App, Avatar, Button, Modal, Select, Tag } from 'antd';
import {
  BookOutlined,
  ExpandOutlined,
  CompressOutlined,
  QuestionCircleOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { useBatch, useWorkspace } from './store';
import { DataBadge } from '../components/ui';
import { WorkspaceLayoutContext } from './WorkspaceLayoutContext';

const pages = [
  { to: '/insights', label: '学情洞察', subtitle: '从测评中看见差异，从证据中形成判断' },
  { to: '/explorer', label: '画像探索', subtitle: '按能力组合找到值得进一步了解的学生' },
  { to: '/data', label: '测评数据中心', nav: '测评数据', subtitle: '上传、检查并管理每一次测评' },
];
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const batch = useBatch();
  const { batches, setBatch } = useWorkspace();
  const [fullscreen, setFullscreen] = useState(false);
  const [help, setHelp] = useState(false);
  const { message } = App.useApp();
  const page = pages.find((p) => pathname.startsWith(p.to)) ?? pages[0]!;
  useEffect(() => {
    document.title = `未然 EDU · ${page.label}`;
  }, [page]);
  useEffect(() => {
    const sync = () => setFullscreen(Boolean(document.fullscreenElement));
    const exitOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && document.fullscreenElement) {
        void document
          .exitFullscreen()
          .catch(() => message.error('退出全屏失败，请点击退出全屏按钮重试。'));
      }
    };
    document.addEventListener('fullscreenchange', sync);
    document.addEventListener('keydown', exitOnEscape);
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      document.removeEventListener('keydown', exitOnEscape);
    };
  }, [message]);
  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      message.error('当前浏览器无法进入全屏，请使用浏览器全屏功能。');
    }
  }
  const batchSelect = (
    <Select
      aria-label="当前测评批次"
      className="batch-select"
      value={batch.id}
      onChange={setBatch}
      prefix={<BookOutlined />}
      options={batches.map((b) => ({ value: b.id, label: b.name }))}
    />
  );
  const fullButton = (
    <Button
      className="fullscreen-button"
      type="text"
      icon={fullscreen ? <CompressOutlined /> : <ExpandOutlined />}
      onClick={toggleFullscreen}
    >
      {fullscreen ? '退出全屏' : '全屏'}
      {fullscreen && <kbd>Esc</kbd>}
    </Button>
  );
  return (
    <div className={`app-shell ${fullscreen ? 'is-fullscreen' : ''}`}>
      <header className="topbar">
        <Link className="brand" href="/insights" aria-label="未然 EDU 首页">
          未然 <span>EDU</span>
        </Link>
        {fullscreen ? (
          <>
            <div className="fullscreen-title">
              {page.label}
              <DataBadge demo={batch.demo} />
            </div>
            <div className="topbar-actions">
              {batchSelect}
              {fullButton}
            </div>
          </>
        ) : (
          <>
            <nav className="main-nav" aria-label="主导航">
              {pages.map((p) => (
                <Link
                  href={p.to}
                  key={p.to}
                  className={pathname === p.to ? 'active' : undefined}
                  aria-current={pathname === p.to ? 'page' : undefined}
                >
                  {p.nav ?? p.label}
                </Link>
              ))}
            </nav>
            <div className="topbar-actions">
              <Button type="text" icon={<QuestionCircleOutlined />} onClick={() => setHelp(true)}>
                帮助
              </Button>
              <div className="teacher-label">
                <Avatar icon={<UserOutlined />} />
                <span>本地工作台</span>
              </div>
            </div>
          </>
        )}
      </header>
      <main className="workspace">
        {!fullscreen && (
          <div className="page-heading">
            <div className="page-title-group">
              <h1>{page.label}</h1>
              <div>
                <DataBadge demo={batch.demo} />
                <p>{page.subtitle}</p>
              </div>
            </div>
            <div className="page-actions">
              {batchSelect}
              {fullButton}
            </div>
          </div>
        )}
        <WorkspaceLayoutContext.Provider value={{ fullscreen }}>
          {children}
        </WorkspaceLayoutContext.Provider>
      </main>
      <footer className="app-footer">
        <span>未然 EDU · 让每一次评价成为教学的起点</span>
        <span>
          {batch.demo
            ? '当前为合成演示数据，来源引用为示例'
            : '当前文件在本地解析，批次仅保留至刷新前'}
          <span className="footer-dot">·</span>本地演示版
        </span>
      </footer>
      <Modal
        open={help}
        onCancel={() => setHelp(false)}
        footer={
          <Button type="primary" onClick={() => setHelp(false)}>
            知道了
          </Button>
        }
        title="使用未然 EDU"
      >
        <div className="help-content">
          <p>
            <b>学情洞察</b> 查看能力概况、群体条件和可追溯的评分依据。
          </p>
          <p>
            <b>画像探索</b> 设置条件并应用，人数、分布和学生列表同步更新。
          </p>
          <p>
            <b>测评数据</b> 下载长表模板，选择评价标准，上传后预览校验，全部通过才能创建独立批次。
          </p>
          <Tag color="blue">本地演示版</Tag>
          <p>
            数据只保留在当前会话内存，刷新后恢复初始状态。教学分析目前提供规则模板，未连接 AI 服务。
          </p>
        </div>
      </Modal>
    </div>
  );
}
