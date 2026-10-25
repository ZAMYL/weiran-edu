'use client';
import { ModePage } from '../../data-mode';
import DemoExplorer from '../../../features/explorer/ExplorerPage';
import LiveExplorer from '../../../features/live/LiveExplorerPage';
export default function ExplorerPage() {
  return <ModePage demo={<DemoExplorer />} live={<LiveExplorer />} />;
}
