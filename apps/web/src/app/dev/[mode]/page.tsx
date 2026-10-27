import { headers } from 'next/headers';
import { notFound } from 'next/navigation';
import DevMode from './DevMode';
export const dynamic = 'force-dynamic';
export default async function DevModePage({ params }: { params: Promise<{ mode: string }> }) {
  const { mode } = await params;
  const host = (await headers()).get('host') ?? '';
  if (
    !/^(127\.0\.0\.1|localhost|\[::1\])(?::\d+)?$/.test(host) ||
    (process.env.EDU_ENABLE_DEMO !== '1' && process.env.NODE_ENV !== 'development') ||
    (mode !== 'demo' && mode !== 'live')
  )
    notFound();
  return <DevMode mode={mode} />;
}
