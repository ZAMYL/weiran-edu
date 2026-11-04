import { ModePage } from '../../data-mode';
import LiveReportsPage from '../../../features/live/LiveReportsPage';
export default function ReportsPage() {
  return (
    <ModePage
      demo={<p>内部演示不生成正式报告，请使用真实测评验证报告流程。</p>}
      live={<LiveReportsPage />}
    />
  );
}
