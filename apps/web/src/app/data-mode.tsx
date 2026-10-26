'use client';

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { Alert, App, Button, Input, Modal, Pagination, Segmented, Select, Tag } from 'antd';
import { ApiError, createEduClient, type EduClient } from '../services/edu-api/client';
import type { ApiBatch, ApiCondition, ApiScope } from '../services/edu-api/types';
import { Panel } from '../components/ui';
import { useRemote } from '../features/live/useRemote';

import {
  readDiscoveryOrigin,
  type DiscoveryOrigin,
} from '../services/edu-api/discovery-navigation';

type Mode = 'live' | 'demo';
interface SessionState {
  mode: Mode;
  testMode: boolean;
  connected: boolean;
  teacherId: string | null;
  scopes: ApiScope[];
  batch: ApiBatch | null;
  conditions: ApiCondition[];
  discoveryOrigin: DiscoveryOrigin | null;
  revision: number;
  notice: string | null;
  loginOpen: boolean;
}
const initial: SessionState = {
  mode: 'live',
  testMode: false,
  connected: false,
  teacherId: null,
  scopes: [],
  batch: null,
  conditions: [],
  discoveryOrigin: null,
  revision: 0,
  notice: null,
  loginOpen: false,
};
interface DataMode extends SessionState {
  client: EduClient;
  setMode: (mode: Mode) => void;
  connect: (token: string | null) => Promise<void>;
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
  const [connecting] = useState(() => {
    let sequence = 0;
    return { next: () => ++sequence, isCurrent: (value: number) => value === sequence };
  });
  const [client] = useState(() =>
    createEduClient(() => {
      connecting.next();
      setState((s) => ({
        ...initial,
        mode: s.mode,
        testMode: s.testMode,
        revision: s.revision + 1,
        notice: s.testMode
          ? '测试数据连接已失效，请重新连接测试数据。'
          : '教师凭证已失效或被撤销，正式数据已清除，请重新连接。',
      }));
    }),
  );
  function disconnect() {
    connecting.next();
    client.clear();
    setState((s) => ({ ...initial, mode: s.mode, testMode: s.testMode, revision: s.revision + 1 }));
  }
  function setMode(mode: Mode) {
    connecting.next();
    client.clear();
    setState((s) => ({ ...initial, mode, testMode: s.testMode, revision: s.revision + 1 }));
  }
  const connect = useCallback(
    async (token: string | null) => {
      const sequence = connecting.next();
      if (token === null) client.useTestSession();
      else client.authenticate(token);
      try {
        const [me, scopes, batches] = await Promise.all([
          client.me(),
          client.scopes(),
          client.batches(),
        ]);
        if (!connecting.isCurrent(sequence)) return;
        const origin = readDiscoveryOrigin(window.location.search);
        let selected = batches.items[0] ?? null;
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
          connected: true,
          teacherId: me.teacher_id,
          scopes: scopes.items,
          batch: selected,
          discoveryOrigin: selected && origin ? origin : null,
          conditions: selected && origin ? origin.spec.scope_conditions : [],
          revision: s.revision + 1,
          notice,
          loginOpen: false,
        }));
      } catch (error) {
        if (connecting.isCurrent(sequence)) {
          client.clear();
          if (token === null)
            setState((s) => ({
              ...s,
              notice: '测试数据连接未完成，请重试；如仍失败请检查本机服务。',
            }));
        }
        throw error;
      }
    },
    [client, connecting],
  );
  useEffect(() => {
    if (state.mode !== 'live') return;
    const controller = new AbortController();
    let active = true;
    void (async () => {
      try {
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
          setState((s) => ({ ...s, testMode: true }));
          await connect(null);
        }
      } catch {
        // Connection failures remain visible through the test-mode gate; never use Demo as fallback.
      }
    })();
    return () => {
      active = false;
      controller.abort();
      connecting.next();
      client.clear();
    };
  }, [state.mode, connect, client, connecting]);
  function clearPointer() {
    if (window.location.search) window.history.replaceState(null, '', window.location.pathname);
  }
  function selectBatch(batch: ApiBatch | null) {
    clearPointer();
    setState((s) => ({
      ...s,
      batch,
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
        connect,
        disconnect,
        setMode,
        openLogin: (loginOpen) => setState((s) => ({ ...s, loginOpen })),
        selectBatch,
        batchChanged: selectBatch,
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
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    if (!token.trim() || busy) return;
    const candidate = token.trim();
    setToken('');
    setError(null);
    setBusy(true);
    try {
      await session.connect(candidate);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError'))
        setError(e instanceof ApiError ? e.message : '连接未完成，请重试。');
    } finally {
      setBusy(false);
    }
  }
  const close = () => {
    if (busy) session.disconnect();
    setToken('');
    setError(null);
    session.openLogin(false);
  };
  return (
    <Modal
      title="连接正式数据 · 教师试点会话"
      open={session.loginOpen}
      onCancel={close}
      destroyOnHidden
      footer={
        <Button type="primary" loading={busy} disabled={!token.trim() || busy} onClick={submit}>
          连接
        </Button>
      }
    >
      <p>使用后端管理员签发的教师临时凭证。凭证仅在内存中使用，刷新或结束会话后需重新输入。</p>
      <label htmlFor="teacher-access-token" className="session-token-label">
        教师临时凭证
      </label>
      <Input.Password
        id="teacher-access-token"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        onPressEnter={submit}
        autoComplete="off"
        maxLength={200}
        placeholder="粘贴教师 access_token"
        disabled={busy}
      />
      {error && <Alert className="session-notice" type="error" showIcon title={error} />}
      <p className="muted small session-notice">
        此入口使用教师权限；不提供管理员操作，不将凭证存入浏览器长期存储。
      </p>
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
      <Panel className="session-gate" title="连接测试数据">
        <p>正在连接本机测试数据。</p>
        {session.notice && <Alert showIcon type="warning" title={session.notice} />}
        <Button
          type="primary"
          onClick={() => {
            void session.connect(null).catch(() => {});
          }}
        >
          重新连接测试数据
        </Button>
      </Panel>
    );
  if (!session.connected)
    return (
      <Panel className="session-gate" title="连接教师会话，查看正式测评数据">
        <p>正式模式从服务端读取授权批次、统计和评分证据。请先连接教师临时凭证。</p>
        {session.notice && <Alert showIcon type="warning" title={session.notice} />}
        <Button type="primary" onClick={() => session.openLogin(true)}>
          连接教师会话
        </Button>
        <p className="muted small">固定演示可从顶部显式切换到 Demo，两种模式的数据不会混用。</p>
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
  const { client, batch, connected, revision, selectBatch } = useDataMode();
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => client.batches(offset, 20, signal),
    [client, offset],
  );
  const resource = useRemote(connected ? `batch-list:${revision}:${offset}` : null, load);
  const { message } = App.useApp();
  const options = resource.data?.items.map((b) => ({ label: b.title, value: b.batch_id })) ?? [];
  if (batch && !options.some((option) => option.value === batch.batch_id))
    options.unshift({ label: batch.title, value: batch.batch_id });
  return (
    <div className="live-batch-picker">
      <Select
        className="batch-select"
        aria-label="当前正式测评批次"
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
