'use client';

import { ArrowRightOutlined } from '@ant-design/icons';
import { useRouter } from 'next/navigation';
import { useEffect, useRef } from 'react';
import { useDataMode } from './data-mode';
import styles from './landing.module.css';

export default function Home() {
  const router = useRouter();
  const { connected, openLogin, ready, testMode } = useDataMode();
  const enterAfterLogin = useRef(false);

  useEffect(() => {
    if (connected && enterAfterLogin.current) {
      enterAfterLogin.current = false;
      router.push('/insights');
    }
  }, [connected, router]);

  function enterWorkspace() {
    if (connected || testMode) {
      router.push('/insights');
      return;
    }
    enterAfterLogin.current = true;
    openLogin(true);
  }

  return (
    <main className={styles.landing}>
      <div className={styles.content}>
        <p className={styles.brand}>
          未然 <i>EDU</i>
        </p>
        <h1 className={styles.title}>
          <span>让每一份测评数据，</span>
          <span>都成为有依据的教学洞察</span>
        </h1>
        <p className={styles.description}>
          智能解析编程测评数据，发现能力差异，生成可追溯的群体画像与教学分析报告。
        </p>
        <button type="button" className={styles.action} disabled={!ready} onClick={enterWorkspace}>
          进入教师工作台
          <ArrowRightOutlined aria-hidden="true" />
        </button>
      </div>
    </main>
  );
}
