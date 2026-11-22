import { notFound } from 'next/navigation';
import { workspacePage, workspacePaths } from '../../workspace-routes';

export function generateStaticParams() {
  return workspacePaths.map((path) => ({ workspace: path.slice(1).split('/') }));
}

export default async function Page({ params }: { params: Promise<{ workspace: string[] }> }) {
  const { workspace } = await params;
  if (!workspacePage('/' + workspace.map(encodeURIComponent).join('/'))) notFound();
  // The workspace layout owns the tab panes so switching routes can preserve their state.
  return null;
}
