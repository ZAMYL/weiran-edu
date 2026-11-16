import { ModePage } from '../../data-mode';
import LiveExplorer from '../../../features/live/LiveExplorerPage';

export default function Page() {
  return <ModePage live={<LiveExplorer />} />;
}
