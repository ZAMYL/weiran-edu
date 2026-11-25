'use client';

import {
  Activity,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname, useRouter } from 'next/navigation';
import {
  App,
  Avatar,
  Button,
  Card,
  Dropdown,
  Empty,
  Layout,
  Menu,
  Modal,
  Space,
  Tabs,
  Tooltip,
  Typography,
  type MenuProps,
} from 'antd';
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  CompressOutlined,
  DashboardOutlined,
  ExpandOutlined,
  FileTextOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  ProfileOutlined,
  SunOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { ModePage, useDataMode } from './data-mode';
import {
  answerAnalysis,
  answerHistory,
  dashboard,
  scoreHistory,
  upload,
  workspacePage,
  workspaceTabKey,
} from './workspace-routes';
import { WorkspaceLayoutContext } from './WorkspaceLayoutContext';
import { setDarkMode, useDarkMode } from './theme-preference';
import WorkspaceDashboard from './WorkspaceDashboard';
import LiveAssessmentPicker from '../features/live/LiveAssessmentPicker';
import LiveDataPage from '../features/live/LiveDataPage';
import UnifiedUploadPage from '../features/live/UnifiedUploadPage';
import LiveInsightsPage from '../features/live/LiveInsightsPage';
import LiveExplorerPage from '../features/live/LiveExplorerPage';
import LiveReportsPage from '../features/live/LiveReportsPage';
import {
  AnswerSheetAnalysisEntry,
  AnswerSheetDetails,
  AnswerSheetLibrary,
} from '../features/live/AnswerSheets';

const menu: MenuProps['items'] = [
  { key: dashboard, label: '仪表盘', icon: <DashboardOutlined aria-hidden="true" /> },
  {
    key: upload,
    label: '上传',
    icon: <CloudUploadOutlined aria-hidden="true" />,
  },
  {
    key: 'assessments',
    label: '测评管理',
    icon: <ProfileOutlined aria-hidden="true" />,
    children: [
      { key: scoreHistory, label: '评分与评价' },
      { key: answerHistory, label: '编程答题卡' },
    ],
  },
  {
    key: 'analysis',
    label: '分析',
    icon: <BarChartOutlined aria-hidden="true" />,
    children: [
      { key: '/insights', label: '学情分析' },
      { key: '/explorer', label: '画像探索' },
      { key: answerAnalysis, label: '答题卡分析' },
    ],
  },
  { key: '/reports', label: '分析报告', icon: <FileTextOutlined aria-hidden="true" /> },
  { key: '/students', label: '学生管理', icon: <TeamOutlined aria-hidden="true" /> },
];

const workspaceTabsStorageKey = 'weiran.edu.workspace-tabs.v1';

// Upload routes share a tab, whose stored path points to its last selected form.
function rememberPath(paths: string[], path: string) {
  if (paths.includes(path)) return paths;
  const key = workspaceTabKey(path);
  return paths.some((item) => workspaceTabKey(item) === key)
    ? paths.map((item) => (workspaceTabKey(item) === key ? path : item))
    : [...paths, path];
}

function readStoredTabs(pathname: string): {
  paths: string[];
  titles: Record<string, string>;
} | null {
  try {
    const raw = sessionStorage.getItem(workspaceTabsStorageKey);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') return null;
    const rawPaths = 'paths' in parsed && Array.isArray(parsed.paths) ? parsed.paths : [];
    const validPaths = rawPaths.filter(
      (item): item is string =>
        typeof item === 'string' && item !== dashboard && Boolean(workspacePage(item)),
    );
    const deduped = rememberPath(validPaths.reduce(rememberPath, [dashboard]), pathname);
    const rawTitles =
      'titles' in parsed && parsed.titles && typeof parsed.titles === 'object' ? parsed.titles : {};
    const titles: Record<string, string> = {};
    for (const [key, value] of Object.entries(rawTitles)) {
      if (deduped.includes(key) && typeof value === 'string' && value.trim()) {
        titles[key] = value;
      }
    }
    return { paths: deduped, titles };
  } catch {
    return null;
  }
}

function writeStoredTabs(paths: string[], titles: Record<string, string>) {
  try {
    sessionStorage.setItem(workspaceTabsStorageKey, JSON.stringify({ paths, titles }));
  } catch {
    /* ignore storage unavailable */
  }
}

function clearStoredTabs() {
  try {
    sessionStorage.removeItem(workspaceTabsStorageKey);
  } catch {
    /* ignore storage unavailable */
  }
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const page = workspacePage(pathname);
  return page ? <WorkspaceShell pathname={pathname} page={page} /> : children;
}

function WorkspaceShell({
  pathname,
  page,
}: {
  pathname: string;
  page: NonNullable<ReturnType<typeof workspacePage>>;
}) {
  const router = useRouter();
  const dark = useDarkMode();
  const { connected, testMode, connect, disconnect, openLogin } = useDataMode();
  const { message } = App.useApp();
  const [fullscreen, setFullscreen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [help, setHelp] = useState(false);
  const [openGroups, setOpenGroups] = useState<string[]>(page.group ? [page.group] : []);
  const [paths, setPaths] = useState(() =>
    pathname === dashboard ? [dashboard] : [dashboard, pathname],
  );
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [closingPath, setClosingPath] = useState<string | null>(null);
  const [tabsReady, setTabsReady] = useState(false);
  const initialPathname = useRef(pathname);
  const [previousConnection, setPreviousConnection] = useState(connected);
  if (previousConnection !== connected) {
    setPreviousConnection(connected);
    if (!connected) {
      setTitles({});
    }
  }
  const [previousPath, setPreviousPath] = useState(pathname);
  if (previousPath !== pathname) {
    setPreviousPath(pathname);
    const remaining = closingPath
      ? paths.filter((path) => workspaceTabKey(path) !== closingPath)
      : paths;
    if (closingPath) setClosingPath(null);
    const nextPaths = rememberPath(remaining, pathname);
    if (nextPaths !== paths) setPaths(nextPaths);
    if (page.group && !openGroups.includes(page.group)) setOpenGroups([...openGroups, page.group]);
  }

  useEffect(() => {
    const stored = readStoredTabs(initialPathname.current);
    if (stored) {
      // Restore open tabs after browser refresh without causing SSR hydration mismatch.
      setPaths((current) => current.reduce(rememberPath, stored.paths));
      setTitles((current) => ({ ...stored.titles, ...current }));
    }
    setTabsReady(true);
  }, []);

  useEffect(() => {
    if (!tabsReady) return;
    writeStoredTabs(paths, titles);
  }, [tabsReady, paths, titles]);

  useEffect(() => {
    const sync = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      message.error('当前浏览器无法切换全屏，请使用浏览器全屏功能。');
    }
  }

  function navigate(path: string) {
    if (path !== pathname) router.push(path, { scroll: false });
  }

  function openPage(key: string) {
    navigate(paths.find((path) => workspaceTabKey(path) === key) ?? key);
  }

  function closePage(key: string) {
    if (key === dashboard) return;
    const position = paths.findIndex((path) => workspaceTabKey(path) === key);
    const path = paths[position];
    if (!path) return;
    const next = paths[position - 1] ?? dashboard;
    if (key === workspaceTabKey(pathname)) {
      setClosingPath(key);
      router.replace(next, { scroll: false });
    } else {
      setPaths((current) => current.filter((item) => item !== path));
    }
    setTitles((current) => {
      const remaining = { ...current };
      delete remaining[path];
      return remaining;
    });
  }

  const updateTitle = useCallback((path: string, title: string) => {
    setTitles((current) => (current[path] === title ? current : { ...current, [path]: title }));
  }, []);
  const openPaths = rememberPath(paths, pathname);

  return (
    <Layout
      hasSider={!fullscreen}
      className={`app-shell workspace-shell${fullscreen ? ' is-fullscreen' : ''}`}
    >
      {!fullscreen && (
        <Layout.Sider
          className="workspace-sider"
          theme="light"
          width={216}
          collapsedWidth={64}
          breakpoint="md"
          collapsible
          collapsed={collapsed}
          onCollapse={setCollapsed}
          trigger={null}
        >
          <Layout.Header className="workspace-brandbar">
            <Link className="workspace-brand" href={dashboard} aria-label="未然 EDU 仪表盘">
              <Image src="/weiran-logo.png" alt="" width={32} height={32} unoptimized />
              {!collapsed && '未然 EDU'}
            </Link>
          </Layout.Header>
          <Menu
            aria-label="工作台导航"
            mode="inline"
            inlineCollapsed={collapsed}
            items={menu}
            selectedKeys={[page.menuKey]}
            openKeys={collapsed ? undefined : openGroups}
            onOpenChange={setOpenGroups}
            onClick={({ key }) => openPage(key)}
          />
          <div className="workspace-sider-footer">
            <Tooltip title={dark ? '切换到浅色主题' : '切换到暗黑主题'}>
              <Button
                type="text"
                shape="circle"
                aria-label="暗黑主题"
                aria-pressed={dark}
                icon={dark ? <SunOutlined /> : <MoonOutlined />}
                onClick={() => setDarkMode(!dark)}
              />
            </Tooltip>
            <Button
              className="workspace-sider-toggle"
              type="text"
              aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => {
                if (collapsed && page.group && !openGroups.includes(page.group)) {
                  setOpenGroups([...openGroups, page.group]);
                }
                setCollapsed(!collapsed);
              }}
            />
          </div>
        </Layout.Sider>
      )}
      <Layout>
        <Layout.Content className="workspace-main">
          {fullscreen && (
            <Layout.Header className="workspace-fullscreen-header">
              <div className="fullscreen-title">{titles[pathname] ?? page.title}</div>
              {page.batch && connected && <LiveAssessmentPicker />}
              <Space>
                {page.assessmentId && (
                  <AnswerSheetBackButton onBack={() => navigate(answerHistory)} />
                )}
                <Button icon={<CompressOutlined />} onClick={() => void toggleFullscreen()}>
                  退出全屏
                </Button>
              </Space>
            </Layout.Header>
          )}
          <Tabs
            className="workspace-tabs"
            aria-label="已打开页面"
            type="editable-card"
            hideAdd
            activeKey={workspaceTabKey(pathname)}
            onChange={openPage}
            onEdit={(target, action) => {
              if (action === 'remove' && typeof target === 'string') closePage(target);
            }}
            items={openPaths.map((path) => ({
              key: workspaceTabKey(path),
              label: titles[path] ?? workspacePage(path)?.title,
              closable: path !== dashboard,
              children: (
                <Activity mode={path === pathname ? 'visible' : 'hidden'}>
                  <WorkspacePane
                    path={path}
                    fullscreen={fullscreen}
                    navigate={navigate}
                    updateTitle={updateTitle}
                  />
                </Activity>
              ),
            }))}
            classNames={{
              header: 'workspace-tabbar',
              body: 'workspace-tab-body',
              content: 'workspace-tab-panel',
            }}
            tabBarExtraContent={{
              right: (
                <Dropdown
                  menu={{
                    items: [
                      connected
                        ? { key: 'disconnect', label: '结束会话' }
                        : { key: 'connect', label: '连接账户' },
                      {
                        key: 'fullscreen',
                        label: '全屏',
                        icon: <ExpandOutlined aria-hidden="true" />,
                      },
                      { key: 'help', label: '帮助' },
                    ],
                    onClick: ({ key }) => {
                      if (key === 'disconnect') {
                        disconnect();
                        clearStoredTabs();
                        setPaths([dashboard]);
                        setTitles({});
                        router.replace(dashboard, { scroll: false });
                      }
                      if (key === 'connect') {
                        if (testMode) void connect(null).catch(() => {});
                        else openLogin(true);
                      }
                      if (key === 'fullscreen') void toggleFullscreen();
                      if (key === 'help') setHelp(true);
                    },
                  }}
                  trigger={['click']}
                >
                  <Button
                    type="text"
                    aria-label={connected ? '教师账户' : '账户菜单'}
                    icon={<Avatar size="small" icon={<UserOutlined />} />}
                  >
                    <span className="workspace-account-label">
                      {connected ? '教师账户' : '账户'}
                    </span>
                  </Button>
                </Dropdown>
              ),
            }}
          />
        </Layout.Content>
      </Layout>
      <Modal
        open={help}
        onCancel={() => setHelp(false)}
        onOk={() => setHelp(false)}
        cancelButtonProps={{ style: { display: 'none' } }}
        okText="知道了"
        title="使用未然 EDU"
      >
        <p>
          从侧栏打开页面，使用顶部页签切换已打开的工作。导入评分表或编程答题卡后，可在测评管理查看记录。
        </p>
      </Modal>
    </Layout>
  );
}

function AnswerSheetBackButton({ onBack }: { onBack: () => void }) {
  return (
    <Button
      aria-label="返回答题卡列表"
      icon={<ArrowLeftOutlined aria-hidden="true" />}
      onClick={onBack}
    >
      返回
    </Button>
  );
}

function WorkspacePane({
  path,
  fullscreen,
  navigate,
  updateTitle,
}: {
  path: string;
  fullscreen: boolean;
  navigate: (path: string) => void;
  updateTitle: (path: string, title: string) => void;
}) {
  const page = workspacePage(path);
  const { batch, connected } = useDataMode();
  const [summaryHost, setSummaryHost] = useState<HTMLElement | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const position = useRef({ top: 0, left: 0 });
  const setTitle = useCallback((title: string) => updateTitle(path, title), [path, updateTitle]);
  useLayoutEffect(() => {
    if (scroll.current) scroll.current.scrollTo(position.current);
  }, []);
  if (!page) return null;
  let content: ReactNode;
  switch (workspaceTabKey(path)) {
    case dashboard:
      content = <WorkspaceDashboard />;
      break;
    case upload:
      content = <UnifiedUploadPage />;
      break;
    case scoreHistory:
      content = <LiveDataPage view="history" />;
      break;
    case answerHistory:
      content = <AnswerSheetLibrary />;
      break;
    case '/insights':
      content = <LiveInsightsPage />;
      break;
    case '/explorer':
      content = <LiveExplorerPage />;
      break;
    case answerAnalysis:
      content = <AnswerSheetAnalysisEntry />;
      break;
    case '/reports':
      content = <LiveReportsPage />;
      break;
    case '/students':
      content = (
        <Card>
          <Empty description="学生名册待接入" />
        </Card>
      );
      break;
    default:
      content = page.assessmentId ? (
        <AnswerSheetDetails assessmentId={page.assessmentId} onTitleChange={setTitle} />
      ) : null;
  }
  const isUploadWorkspace = page.menuKey === upload;
  return (
    <div
      className={`workspace-scroll${path === '/reports' ? ' is-report-page' : ''}${path === '/explorer' ? ' is-explorer-page' : ''}`}
      ref={scroll}
      onScroll={(event) => {
        const { scrollTop, scrollLeft, clientHeight } = event.currentTarget;
        if (clientHeight) position.current = { top: scrollTop, left: scrollLeft };
      }}
    >
      <div className={`workspace${isUploadWorkspace ? ' upload-workspace' : ''}`}>
        {(!fullscreen || page.batch) && (
          <div className={`page-heading${isUploadWorkspace ? ' upload-workspace-heading' : ''}`}>
            <div className="workspace-intro">
              {!fullscreen && (
                <div
                  className={`page-title-group${page.assessmentId ? ' answer-sheet-detail-heading' : ''}`}
                >
                  <Typography.Title level={1}>{page.title}</Typography.Title>
                  {page.assessmentId && (
                    <AnswerSheetBackButton onBack={() => navigate(answerHistory)} />
                  )}
                </div>
              )}
              {!fullscreen && page.batch && connected && batch && (
                <section className="assessment-toolbar" aria-label="当前测评工作台">
                  {path === '/reports' && (
                    <Typography.Text className="assessment-toolbar-label" type="secondary">
                      当前测评：
                    </Typography.Text>
                  )}
                  <LiveAssessmentPicker />
                </section>
              )}
            </div>
            <div className="workspace-summary" ref={setSummaryHost} />
          </div>
        )}
        <WorkspaceLayoutContext.Provider value={{ fullscreen, summaryHost }}>
          <ModePage live={content} />
        </WorkspaceLayoutContext.Provider>
      </div>
    </div>
  );
}
