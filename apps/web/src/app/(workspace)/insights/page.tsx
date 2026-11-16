import { ModePage } from '../../data-mode';
import LiveInsights from '../../../features/live/LiveInsightsPage';

export default function Page() {
  return <ModePage live={<LiveInsights />} />;
}
