import { ModePage } from '../../data-mode';
import LiveReportsPage from '../../../features/live/LiveReportsPage';
export default function ReportsPage() {
  return <ModePage demo={<p>当前测评暂不支持生成或导出报告。</p>} live={<LiveReportsPage />} />;
}
