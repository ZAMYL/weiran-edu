import { ModePage } from '../../data-mode';
import LiveReportsPage from '../../../features/live/LiveReportsPage';

export default function Page() {
  return <ModePage live={<LiveReportsPage />} />;
}
