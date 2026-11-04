'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useDataMode } from '../../data-mode';
export default function DevMode({ mode }: { mode: 'live' | 'demo' }) {
  const session = useDataMode();
  const router = useRouter();
  useEffect(() => {
    if (session.mode !== mode) session.setMode(mode);
    else router.replace(mode === 'demo' ? '/insights' : '/data');
  }, [session, mode, router]);
  return <p>正在打开内部验证环境…</p>;
}
