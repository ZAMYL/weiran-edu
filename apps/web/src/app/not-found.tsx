import { Result } from 'antd';
import Link from 'next/link';

export default function NotFound() {
  return (
    <main>
      <Result status="404" title={<h1>页面不存在</h1>} extra={<Link href="/">返回首页</Link>} />
    </main>
  );
}
