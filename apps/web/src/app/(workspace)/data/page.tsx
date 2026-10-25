'use client';
import { ModePage } from '../../data-mode';
import DemoData from '../../../features/assessments/DataPage';
import LiveData from '../../../features/live/LiveDataPage';
export default function DataPage() {
  return <ModePage demo={<DemoData />} live={<LiveData />} />;
}
