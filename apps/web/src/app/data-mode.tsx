'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Alert, App, Button, Modal, Pagination, Segmented, Select, Tag } from 'antd';
import TeacherLoginForm from '../features/auth/TeacherLoginForm';
import loginStyles from '../features/auth/teacher-login.module.css';
import { ApiError, createEduClient, type EduClient } from '../services/edu-api/client';
import { signIn, signOut, type AccountLogin } from '../services/edu-api/account-session';
import type {
  ApiBatch,
  ApiCommit,
  ApiCondition,
  ApiImport,
  ApiScope,
  InsightSpec,
} from '../services/edu-api/types';
import { Panel } from '../components/ui';
import { useRemote } from '../features/live/useRemote';

import {
  readDiscoveryOrigin,
  type DiscoveryOrigin,
} from '../services/edu-api/discovery-navigation';

import type { DiscoverySelection } from '../services/edu-api/analysis-types';
import type { QuestionPreview } from '../services/edu-api/question-types';
export interface ReportDraft {
  key: string;
  spec: InsightSpec;
  discovery?: DiscoverySelection;
  names: { id: string; title: string }[];
  showSaved?: boolean;
}
type Mode = 'live' | 'demo';
type SessionKind = 'account' | 'credential' | 'test';
export interface UploadDraft {
  file: File | null;
  title: string;
  course: string;
  date: string;
  scope: string;
  preview: ApiImport | QuestionPreview | null;
  committed: ApiCommit | null;
}
interface SessionState {
  activeReport: { batchId: string; snapshotId: string; resultId: string } | null;
  uploadDraft: UploadDraft | null;
  mode: Mode;
  assessmentPanel: 'upload' | 'history' | null;
  assessmentRevision: number;
  ready: boolean;
  reportDraft: ReportDraft | null;
  testMode: boolean;
  syntheticAi: boolean;
  connected: boolean;
  sessionKind: SessionKind | null;
  teacherId: string | null;
  scopes: ApiScope[];
  batch: ApiBatch | null;
  conditions: ApiCondition[];
  discoveryOrigin: DiscoveryOrigin | null;
  revision: number;
  notice: string | null;
  loginOpen: boolean;
  logoutPending: boolean;
}
const initial: SessionState = {
  activeReport: null,
  uploadDraft: null,
  mode: 'live',
  assessmentPanel: null,
  assessmentRevision: 0,
  ready: false,
  reportDraft: null,
  testMode: false,
  syntheticAi: false,
  connected: false,
  sessionKind: null,
  teacherId: null,
  scopes: [],
  batch: null,
  conditions: [],
  discoveryOrigin: null,
  revision: 0,
  notice: null,
  loginOpen: false,
  logoutPending: false,
};
const logoutKey = 'weiran.edu.logout-pending';
function rememberLogout(pending: boolean) {
  try {
    if (pending) sessionStorage.setItem(logoutKey, '1');
    else sessionStorage.removeItem(logoutKey);
  } catch {
    // Storage is optional and never holds credentials.
  }
}

function hasPendingLogout() {
  try {
    return sessionStorage.getItem(logoutKey) === '1';
  } catch {
    return false;
  }
}
interface DataMode extends SessionState {
  setActiveReport: (report: SessionState['activeReport']) => void;
  setUploadDraft: (draft: UploadDraft | null) => void;
  client: EduClient;
  setReportDraft: (draft: ReportDraft | null) => void;
  openAssessments: (panel: 'upload' | 'history' | null) => void;
  setMode: (mode: Mode) => void;
  connect: (token: string | null) => Promise<void>;
  login: (input: AccountLogin) => Promise<void>;
  requireSession: (action: () => void) => boolean;
  disconnect: () => void;
  openLogin: (open: boolean) => void;
  selectBatch: (batch: ApiBatch | null) => void;
  setConditions: (conditions: ApiCondition[]) => void;
  exploreDiscovery: (origin: DiscoveryOrigin) => void;
  batchChanged: (batch: ApiBatch) => void;
}
const Context = createContext<DataMode | null>(null);
export function useDataMode() {
  const value = useContext(Context);
  if (!value) throw new Error('DATA_MODE_PROVIDER_MISSING');
  return value;
}
export function DataModeProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>(initial);
  const pendingAction = useRef<(() => void) | null>(null);
  const [connecting] = useState(() => {
    let sequence = 0;
    return { next: () => ++sequence, isCurrent: (value: number) => value === sequence };
  });
  const [client] = useState(() =>
    createEduClient(() => {
      connecting.next();
      setState((s) => ({
        ...initial,
        ready: true,
        mode: s.mode,
        testMode: s.testMode,
        loginOpen: s.loginOpen,
        revision: s.revision + 1,
        notice: s.connected
          ? s.testMode
            ? '连接已失效，请重新连接。'
            : '登录已失效，请重新登录。'
          : s.notice,
      }));
    }),
  );
  async function disconnect() {
    pendingAction.current = null;
    const account = state.sessionKind === 'account' || state.logoutPending;
    const sequence = connecting.next();
    client.clear();
    setState((s) => ({
      ...initial,
      ready: !account,
      mode: s.mode,
      testMode: s.testMode,
      revision: s.revision + 1,
    }));
    if (account) {
      rememberLogout(true);
      let notice: string | null = null;
      try {
        await signOut();
        rememberLogout(false);
      } catch (error) {
        notice =
          error instanceof ApiError && error.code === 'LOGOUT_NOT_CONFIRMED'
            ? error.message
            : '无法确认退出，请检查连接后重试。';
      }
      if (connecting.isCurrent(sequence))
        setState((s) => ({ ...s, ready: true, notice, logoutPending: notice !== null }));
    }
  }
  function setMode(mode: Mode) {
    pendingAction.current = null;
    connecting.next();
    client.clear();
    setState((s) => ({
      ...initial,
      ready: mode === 'demo',
      mode,
      testMode: s.testMode,
      revision: s.revision + 1,
    }));
  }
  const establish = useCallback(
    async (kind: SessionKind, token?: string) => {
      const sequence = connecting.next();
      if (kind === 'test') client.useTestSession();
      else if (kind === 'account') client.useAccountSession();
      else if (token) client.authenticate(token);
      else throw new ApiError(401, 'UNAUTHORIZED');
      try {
        const me = await client.me();
        if (!connecting.isCurrent(sequence)) return;
        const [scopes, batches] = await Promise.all([client.scopes(), client.batches()]);
        if (!connecting.isCurrent(sequence)) return;
        const origin = readDiscoveryOrigin(window.location.search);
        let selected = batches.items[0] ?? null;
        if (!origin) {
          let pointer: string | null = null;
          try {
            pointer = localStorage.getItem(`weiran.edu.current-batch.${me.teacher_id}`);
          } catch {
            /* A pointer is optional, never cached analysis or authorization. */
          }
          if (pointer && /^[a-zA-Z0-9_-]{1,100}$/.test(pointer)) {
            try {
              selected = await client.batch(pointer);
            } catch (error) {
              if (!(error instanceof ApiError) || ![403, 404].includes(error.status)) throw error;
            }
          }
        }
        let notice: string | null = null;
        if (origin) {
          try {
            selected = await client.batch(origin.batch_id);
            if (selected.snapshot_id !== origin.spec.snapshot_id) throw new Error('STALE_SNAPSHOT');
          } catch {
            selected = null;
            notice = '原发现链接已失效或无权访问，请重新选择授权批次。';
          }
        }
        if (!connecting.isCurrent(sequence)) return;
        setState((s) => ({
          ...s,
          ready: true,
          connected: true,
          sessionKind: kind,
          reportDraft: null,
          activeReport: null,
          uploadDraft: null,
          assessmentPanel: null,
          teacherId: me.teacher_id,
          scopes: scopes.items,
          batch: selected,
          discoveryOrigin: selected && origin ? origin : null,
          conditions: selected && origin ? origin.spec.scope_conditions : [],
          revision: s.revision + 1,
          notice,
          loginOpen: false,
          logoutPending: false,
        }));
      } catch (error) {
        if (connecting.isCurrent(sequence)) {
          client.clear();
          if (kind === 'test')
            setState((s) => ({
              ...s,
              notice: '数据连接未完成，请重试。',
            }));
        }
        throw error;
      }
    },
    [client, connecting],
  );
  async function login(input: AccountLogin) {
    if (!state.ready || state.logoutPending) throw new ApiError(409, 'REQUEST_FAILED');
    await signIn(input);
    try {
      await establish('account');
    } catch (error) {
      await signOut().catch(() => {});
      throw error;
    }
  }
  function connect(token: string | null) {
    return establish(token === null ? 'test' : 'credential', token ?? undefined);
  }
  function openLogin(open: boolean) {
    if (!open) pendingAction.current = null;
    if (!open || (state.ready && !state.logoutPending))
      setState((s) => ({ ...s, loginOpen: open }));
  }
  function requireSession(action: () => void) {
    if (state.connected || state.mode === 'demo') return true;
    if (!state.ready || state.logoutPending) return false;
    pendingAction.current = action;
    if (state.testMode)
      void establish('test').catch(() => {
        pendingAction.current = null;
      });
    else openLogin(true);
    return false;
  }
  useEffect(() => {
    if (state.mode !== 'live') return;
    const controller = new AbortController();
    let active = true;
    void (async () => {
      try {
        if (hasPendingLogout()) {
          try {
            await signOut();
            rememberLogout(false);
          } catch {
            if (active)
              setState((s) => ({ ...s, logoutPending: true, notice: '无法确认退出，请重试。' }));
            return;
          }
        }
        if (!active) return;
        const response = await fetch('/api/edu/test-session', {
          cache: 'no-store',
          credentials: 'omit',
          signal: controller.signal,
        });
        const config: unknown = await response.json();
        if (
          active &&
          response.ok &&
          config &&
          typeof config === 'object' &&
          'enabled' in config &&
          config.enabled === true
        ) {
          setState((s) => ({
            ...s,
            testMode: true,
            syntheticAi: 'ai_synthetic_enabled' in config && config.ai_synthetic_enabled === true,
          }));
          await establish('test');
        } else if (active) {
          await establish('account');
        }
      } catch (error) {
        if (active && error instanceof ApiError && error.status !== 401)
          setState((s) => ({ ...s, notice: s.notice ?? '暂时无法验证登录状态，请重试。' }));
      } finally {
        if (active) setState((s) => ({ ...s, ready: true }));
      }
    })();
    return () => {
      active = false;
      controller.abort();
      connecting.next();
      client.clear();
    };
  }, [state.mode, establish, client, connecting]);
  useEffect(() => {
    if (!state.connected || !pendingAction.current) return;
    const action = pendingAction.current;
    pendingAction.current = null;
    action();
  }, [state.connected]);
  useEffect(() => {
    if (!state.connected || state.sessionKind === 'test') return;
    const controller = new AbortController();
    let checking = false;
    async function check() {
      if (document.visibilityState !== 'visible' || checking) return;
      checking = true;
      try {
        await client.me(controller.signal);
      } catch {
        // Only an explicit 401 clears the session. A temporary outage does not.
      } finally {
        checking = false;
      }
    }
    const timer = window.setInterval(check, 60_000);
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', check);
    return () => {
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener('focus', check);
      document.removeEventListener('visibilitychange', check);
    };
  }, [client, state.connected, state.sessionKind]);
  useEffect(() => {
    if (!state.connected || !state.teacherId || !state.batch) return;
    try {
      localStorage.setItem(`weiran.edu.current-batch.${state.teacherId}`, state.batch.batch_id);
    } catch {
      /* Backend recovery still works when storage is unavailable. */
    }
  }, [state.connected, state.teacherId, state.batch]);
  function clearPointer() {
    if (window.location.search) window.history.replaceState(null, '', window.location.pathname);
  }
  function selectBatch(batch: ApiBatch | null) {
    clearPointer();
    setState((s) => ({
      ...s,
      batch,
      assessmentPanel: null,
      reportDraft: null,
      activeReport: null,
      discoveryOrigin: null,
      conditions: [],
      revision: s.revision + 1,
    }));
  }
  return (
    <Context.Provider
      value={{
        ...state,
        client,
        openAssessments: (assessmentPanel) =>
          setState((s) => ({
            ...s,
            assessmentPanel,
            assessmentRevision: s.assessmentRevision + 1,
          })),
        setReportDraft: (reportDraft) =>
          setState((s) => ({
            ...s,
            reportDraft,
            activeReport: reportDraft ? null : s.activeReport,
          })),
        setActiveReport: (activeReport) => setState((s) => ({ ...s, activeReport })),
        setUploadDraft: (uploadDraft) => setState((s) => ({ ...s, uploadDraft })),
        connect,
        login,
        requireSession,
        disconnect,
        setMode,
        openLogin,
        selectBatch,
        batchChanged: (batch) => {
          selectBatch(batch);
          setState((s) => ({ ...s, uploadDraft: null }));
        },
        exploreDiscovery: (discoveryOrigin) =>
          setState((s) => ({
            ...s,
            discoveryOrigin,
            conditions: discoveryOrigin.spec.scope_conditions.map((c) => ({ ...c })),
            revision: s.revision + 1,
          })),
        setConditions: (conditions) => {
          clearPointer();
          setState((s) => ({
            ...s,
            discoveryOrigin: null,
            reportDraft: null,
            conditions: conditions.map((c) => ({ ...c })),
          }));
        },
      }}
    >
      {children}
      {!state.testMode && <SessionDialog />}
    </Context.Provider>
  );
}
function SessionDialog() {
  const session = useDataMode();
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="教师登录"
      open={session.loginOpen}
      onCancel={() => session.openLogin(false)}
      keyboard={!busy}
      closable={{ disabled: busy }}
      destroyOnHidden
      centered
      width={440}
      classNames={{ mask: loginStyles.mask }}
      styles={{
        container: { padding: '28px clamp(20px, 5vw, 34px) 20px' },
        mask: { backgroundColor: 'rgb(20 36 72 / 34%)' },
        title: { marginBottom: 22, fontSize: 26, lineHeight: 1.25 },
      }}
      mask={{ closable: false }}
      footer={null}
    >
      <TeacherLoginForm
        busy={busy}
        loginWithAccount={async (input) => {
          setBusy(true);
          try {
            await session.login(input);
          } finally {
            setBusy(false);
          }
        }}
        connectWithCredential={async (credential) => {
          setBusy(true);
          try {
            await session.connect(credential);
          } finally {
            setBusy(false);
          }
        }}
      />
    </Modal>
  );
}
export function ModeControls() {
  const mode = useDataMode();
  return (
    <div className="mode-controls">
      <Segmented
        aria-label="数据模式"
        value={mode.mode}
        options={[
          { label: '正式数据', value: 'live' },
          { label: 'Demo', value: 'demo' },
        ]}
        onChange={(value) => mode.setMode(value as Mode)}
      />
      {mode.mode === 'live' && mode.testMode && <Tag color="cyan">联调测试</Tag>}
      {mode.mode === 'live' && !mode.testMode && (
        <Button
          type="text"
          size="small"
          onClick={() => (mode.connected ? mode.disconnect() : mode.openLogin(true))}
        >
          {mode.connected ? '结束会话' : '连接教师'}
        </Button>
      )}
    </div>
  );
}
export function ModeBadge() {
  const { mode, connected, testMode } = useDataMode();
  return (
    <Tag className="data-badge" color={mode === 'demo' ? 'blue' : 'cyan'}>
      {mode === 'demo'
        ? 'Demo · 合成演示'
        : testMode
          ? connected
            ? '联调测试 · 已连接'
            : '联调测试 · 连接中'
          : connected
            ? '正式数据 · 教师会话'
            : '正式数据 · 未连接'}
    </Tag>
  );
}
export function ModePage({ demo, live }: { demo: ReactNode; live: ReactNode }) {
  const session = useDataMode();
  if (session.mode === 'demo') return demo;
  if (!session.connected && session.testMode)
    return (
      <Panel className="session-gate" title="正在连接数据">
        <p>正在读取有权查看的测评。</p>
        {session.notice && <Alert showIcon type="warning" title={session.notice} />}
        <Button
          type="primary"
          onClick={() => {
            void session.connect(null).catch(() => {});
          }}
        >
          重新连接
        </Button>
      </Panel>
    );
  if (!session.connected)
    return (
      <Panel className="session-gate" title="连接账户，查看测评">
        <p>连接后可查看有权访问的测评、评分和分析报告。</p>
        {session.notice && <Alert showIcon type="warning" title={session.notice} />}
        <Button
          type="primary"
          disabled={!session.ready}
          onClick={() =>
            session.logoutPending ? void session.disconnect() : session.openLogin(true)
          }
        >
          {session.logoutPending ? '重试退出' : '登录'}
        </Button>
      </Panel>
    );
  return (
    <div key={session.revision} className="live-workspace">
      {session.notice && <Alert type="warning" showIcon title={session.notice} />}
      {live}
    </div>
  );
}
export function LiveBatchSelect() {
  const { client, batch, connected, revision, selectBatch, syntheticAi } = useDataMode();
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => client.batches(offset, 20, signal),
    [client, offset],
  );
  const resource = useRemote(connected ? `batch-list:${revision}:${offset}` : null, load);
  const { message } = App.useApp();
  const batchLabel = (title: string) => (syntheticAi ? `合成测评 · ${title}` : title);
  const options =
    resource.data?.items.map((b) => ({ label: batchLabel(b.title), value: b.batch_id })) ?? [];
  if (batch && !options.some((option) => option.value === batch.batch_id))
    options.unshift({ label: batchLabel(batch.title), value: batch.batch_id });
  return (
    <div className="live-batch-picker">
      <Select
        className="batch-select"
        aria-label={syntheticAi ? '当前合成测评批次' : '当前正式测评批次'}
        value={batch?.batch_id}
        placeholder={connected ? '选择授权测评批次' : '连接教师后选择批次'}
        disabled={!connected}
        loading={resource.loading}
        options={options}
        onChange={(value) => {
          const target = resource.data?.items.find((b) => b.batch_id === value);
          if (target) selectBatch(target);
        }}
        popupRender={(menu) => (
          <>
            {menu}
            {resource.error && (
              <div className="batch-picker-error">
                <Button
                  type="link"
                  onClick={() => {
                    message.error(resource.error!.message);
                    resource.retry();
                  }}
                >
                  批次读取失败，重试
                </Button>
              </div>
            )}
            {resource.data && resource.data.total_count > 20 && (
              <Pagination
                simple
                current={Math.floor(offset / 20) + 1}
                total={resource.data.total_count}
                pageSize={20}
                showSizeChanger={false}
                onChange={(page) => setOffset((page - 1) * 20)}
              />
            )}
          </>
        )}
      />
    </div>
  );
}
