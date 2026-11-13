'use client';

import { useId, useRef, useState } from 'react';
import { Alert, Button, Input } from 'antd';
import { ApiError } from '../../services/edu-api/client';
import type { AccountLogin } from '../../services/edu-api/account-session';
import styles from './teacher-login.module.css';

export default function TeacherLoginForm({
  connectWithCredential,
  loginWithAccount,
  busy,
}: {
  connectWithCredential: (credential: string) => Promise<void>;
  loginWithAccount: (input: AccountLogin) => Promise<void>;
  busy: boolean;
}) {
  const id = useId();
  const [credentialMode, setCredentialMode] = useState(false);
  const [credential, setCredential] = useState('');
  const [account, setAccount] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || submitting.current) return;
    setError(null);
    const token = credential.trim();
    if (credentialMode ? !token : !account.trim() || !password) return;
    submitting.current = true;
    setCredential('');
    setPassword('');
    try {
      if (credentialMode) await connectWithCredential(token);
      else await loginWithAccount({ account: account.trim(), password });
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      setError(cause instanceof ApiError ? cause.message : '登录未完成，请重试。');
    } finally {
      submitting.current = false;
    }
  }

  return (
    <form className={styles.form} method="post" onSubmit={submit}>
      {credentialMode ? (
        <div className={styles.field}>
          <label htmlFor="teacher-access-token">访问凭据</label>
          <Input.Password
            id="teacher-access-token"
            value={credential}
            onChange={(event) => setCredential(event.target.value)}
            autoComplete="off"
            maxLength={200}
            placeholder="请输入教师访问凭据"
            disabled={busy}
            required
          />
        </div>
      ) : (
        <>
          <div className={styles.field}>
            <label htmlFor={`${id}-account`}>账号</label>
            <Input
              id={`${id}-account`}
              value={account}
              onChange={(event) => setAccount(event.target.value)}
              placeholder="请输入账号"
              autoComplete="username"
              maxLength={128}
              disabled={busy}
              required
            />
          </div>
          <div className={styles.field}>
            <label htmlFor={`${id}-password`}>密码</label>
            <Input.Password
              id={`${id}-password`}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="请输入密码"
              autoComplete="current-password"
              maxLength={256}
              disabled={busy}
              required
            />
          </div>
        </>
      )}
      {error && <Alert type="error" title={error} showIcon />}
      <Button
        className={styles.submit}
        type="primary"
        htmlType="submit"
        loading={busy}
        disabled={busy || (credentialMode ? !credential.trim() : !account.trim() || !password)}
      >
        登录
      </Button>
      <Button
        className={styles.switchMethod}
        type="link"
        disabled={busy}
        onClick={() => {
          setCredential('');
          setPassword('');
          setError(null);
          setCredentialMode(!credentialMode);
        }}
      >
        {credentialMode ? '使用账号密码' : '使用访问凭据'}
      </Button>
    </form>
  );
}
