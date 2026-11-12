'use client';

import { useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { App, Button, Modal, Select } from 'antd';
import {
  BookOutlined,
  ExpandOutlined,
  CompressOutlined,
  QuestionCircleOutlined,
  UploadOutlined,
  HistoryOutlined,
  FileTextOutlined,
  LeftOutlined,
} from '@ant-design/icons';
import { useBatch, useWorkspace } from './store';
import { LiveBatchSelect, useDataMode } from './data-mode';
import { WorkspaceLayoutContext } from './WorkspaceLayoutContext';

const pages = [
  { to: '/insights', label: '学情诊断', subtitle: '发现值得关注的现象，结合依据核查' },
  { to: '/explorer', label: '画像探索', subtitle: '应用条件，核对原始群体与评分依据' },
  { to: '/reports', label: '分析报告', subtitle: '整理发现、保存核验并导出报告' },
];
export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const dataMode = useDataMode();
  const batch = useBatch();
  const { batches, setBatch } = useWorkspace();
  const [fullscreen, setFullscreen] = useState(false);
  const [summaryHost, setSummaryHost] = useState<HTMLElement | null>(null);
  const [help, setHelp] = useState(false);
  const { message } = App.useApp();
  const page = pages.find((p) => pathname.startsWith(p.to)) ?? pages[0]!;
  const uploadWorkspace =
    dataMode.mode === 'live' &&
    pathname === '/insights' &&
    (dataMode.assessmentPanel === 'upload' ||
      (dataMode.connected && !dataMode.batch && dataMode.assessmentPanel !== 'history'));
  const firstUpload =
    uploadWorkspace &&
    dataMode.connected &&
    !dataMode.batch &&
    !dataMode.notice &&
    dataMode.assessmentPanel === null;
  useEffect(() => {
    document.title = '未然 EDU｜AI 时代的编程能力与学习成效诊断平台';
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
  const batchSelect =
    dataMode.mode === 'live' ? (
      <LiveBatchSelect />
    ) : (
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
    <div
      className={`app-shell ${fullscreen ? 'is-fullscreen' : ''} ${pathname === '/reports' ? 'is-report-page' : ''} ${pathname === '/explorer' ? 'is-explorer-page' : ''}`}
    >
      <header className="topbar">
        <Link className="brand" href="/" aria-label="未然 EDU 首页">
          未然 <span>EDU</span>
        </Link>
        {fullscreen ? (
          <>
            <div className="fullscreen-title">{page.label}</div>
            <div className="topbar-actions">
              {!uploadWorkspace && batchSelect}
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
                  onClick={() => dataMode.openAssessments(null)}
                  className={pathname === p.to ? 'active' : undefined}
                  aria-current={pathname === p.to ? 'page' : undefined}
                >
                  {p.label}
                </Link>
              ))}
            </nav>
            <div className="topbar-actions">
              {dataMode.connected ? (
                <span className="account-status">教师账户</span>
              ) : (
                <Button
                  type="text"
                  onClick={() =>
                    dataMode.testMode ? void dataMode.connect(null) : dataMode.openLogin(true)
                  }
                >
                  连接账户
                </Button>
              )}
              {dataMode.connected && (
                <Button type="text" onClick={dataMode.disconnect}>
                  结束会话
                </Button>
              )}
              {fullButton}
              <Button type="text" icon={<QuestionCircleOutlined />} onClick={() => setHelp(true)}>
                帮助
              </Button>
            </div>
          </>
        )}
      </header>
      <main className={`workspace${uploadWorkspace ? ' upload-workspace' : ''}`}>
        <div className={`page-heading${uploadWorkspace ? ' upload-workspace-heading' : ''}`}>
          <div className="workspace-intro">
            {!fullscreen && (
              <div className="page-title-group">
                <h1>{uploadWorkspace ? '上传测评资料' : page.label}</h1>
                {dataMode.mode === 'demo' && <span className="muted">合成测评</span>}
              </div>
            )}
            {!fullscreen && (
              <section className="assessment-toolbar" aria-label="当前测评工作台">
                {!uploadWorkspace && batchSelect}
                {!uploadWorkspace && (
                  <Button
                    className="assessment-upload-action"
                    type="primary"
                    icon={<UploadOutlined aria-hidden="true" />}
                    onClick={() => {
                      dataMode.openAssessments('upload');
                      router.push(dataMode.mode === 'demo' ? '/data' : '/insights');
                    }}
                  >
                    上传新测评
                  </Button>
                )}
                {!firstUpload && (
                  <Button
                    className="assessment-history-action"
                    icon={
                      uploadWorkspace ? (
                        <FileTextOutlined aria-hidden="true" />
                      ) : (
                        <HistoryOutlined aria-hidden="true" />
                      )
                    }
                    aria-label="查看历史测评"
                    onClick={() => {
                      dataMode.openAssessments('history');
                      router.push(dataMode.mode === 'demo' ? '/data' : '/insights');
                    }}
                  >
                    {uploadWorkspace ? '历史测评' : '查看历史测评'}
                  </Button>
                )}
                {dataMode.assessmentPanel && dataMode.batch && (
                  <Button
                    type="link"
                    icon={uploadWorkspace ? <LeftOutlined aria-hidden="true" /> : undefined}
                    aria-label="返回当前诊断"
                    onClick={() => {
                      dataMode.openAssessments(null);
                      router.push(dataMode.mode === 'demo' ? '/data' : '/insights');
                    }}
                  >
                    {uploadWorkspace ? '返回诊断' : '返回当前诊断'}
                  </Button>
                )}
              </section>
            )}
          </div>
          <div className="workspace-summary" ref={setSummaryHost} />
        </div>
        <WorkspaceLayoutContext.Provider value={{ fullscreen, summaryHost }}>
          {children}
        </WorkspaceLayoutContext.Provider>
      </main>
      <footer className="app-footer">
        <span>未然 EDU｜AI 时代的编程能力与学习成效诊断平台</span>
        <span>评分与发现供授权教师核查使用</span>
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
            <b>学情诊断</b> 查看能力概况、群体条件和可追溯的评分依据。
          </p>
          <p>
            <b>画像探索</b> 设置条件并应用，人数、分布和学生列表同步更新。
          </p>
          <p>
            <b>上传测评资料</b> 选择 .xlsx 文件，检查后确认导入。
          </p>
          <p>
            <b>分析报告</b> 保存本次范围及选定发现，核验后导出报告。教师确认不等于因果证明。
          </p>
        </div>
      </Modal>
    </div>
  );
}
