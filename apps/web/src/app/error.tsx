'use client';

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main>
      <h1>页面暂时无法加载</h1>
      <p role="alert">请重试。</p>
      <button type="button" onClick={reset}>
        重试
      </button>
    </main>
  );
}
