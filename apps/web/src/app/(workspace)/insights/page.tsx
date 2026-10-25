'use client';
import { ModePage } from '../../data-mode';
import DemoInsights from '../../../features/insights/InsightsPage';
import LiveInsights from '../../../features/live/LiveInsightsPage';
export default function InsightsPage() {
  return <ModePage demo={<DemoInsights />} live={<LiveInsights />} />;
}
