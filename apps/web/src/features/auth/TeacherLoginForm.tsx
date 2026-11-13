'use client';

import { useId, useState } from 'react';
import { Alert, Button, Input } from 'antd';
import { ApiError } from '../../services/edu-api/client';
import styles from './teacher-login.module.css';

export default function TeacherLoginForm({
  connectWithCredential,
  busy,
}: {
  connectWithCredential: (credential: string) => Promise<void>;
  busy: boolean;
}) {
  const id = useId();
  const [credentialMode, setCredentialMode] = useState(false);
  const [credential, setCredential] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setError(null);

    if (!credentialMode) {
      setPassword('');
      setError('账号密码登录暂未开通，请使用教师访问凭据。');
      return;
    }

    const token = credential.trim();
    if (!token) return;
    setCredential('');
    try {
      await connectWithCredential(token);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      setError(cause instanceof ApiError ? cause.message : '登录未完成，请重试。');
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
            <Input id={`${id}-account`} placeholder="请输入账号" autoComplete="username" required />
          </div>
          <div className={styles.field}>
            <label htmlFor={`${id}-password`}>密码</label>
            <Input.Password
              id={`${id}-password`}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="请输入密码"
              autoComplete="current-password"
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
        disabled={busy || (credentialMode && !credential.trim())}
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
