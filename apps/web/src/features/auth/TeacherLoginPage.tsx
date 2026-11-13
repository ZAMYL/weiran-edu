'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useDataMode } from '../../app/data-mode';
import TeacherLoginForm from './TeacherLoginForm';
import styles from './teacher-login.module.css';

export default function TeacherLoginPage() {
  const { connected, connect } = useDataMode();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (connected) router.replace('/insights');
  }, [connected, router]);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Link href="/" className="brand" aria-label="未然 EDU 首页">
          未然 <span>EDU</span>
        </Link>
      </header>
      <main className={styles.content}>
        <div className={styles.illustration} aria-hidden="true" />
        <section className={styles.card} aria-labelledby="teacher-login-heading">
          <h1 id="teacher-login-heading">教师登录</h1>
          <TeacherLoginForm
            busy={busy}
            connectWithCredential={async (credential) => {
              setBusy(true);
              try {
                await connect(credential);
              } finally {
                setBusy(false);
              }
            }}
          />
        </section>
      </main>
    </div>
  );
}
