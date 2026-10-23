import Link from 'next/link';

export default function NotFound() {
  return (
    <main>
      <h1>页面不存在</h1>
      <Link href="/">返回首页</Link>
    </main>
  );
}
