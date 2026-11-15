'use client';
import { useDataMode } from '../../app/data-mode';
import { Button } from 'antd';
import { useRouter } from 'next/navigation';
import { Panel } from '../../components/ui';
import { NoBatch } from './LiveStatus';
import { ReportWorkspace, reportContextKey } from './AnalysisActions';

export default function LiveReportsPage() {
  const { batch, conditions, openAssessments } = useDataMode();
  const router = useRouter();
  if (!batch) return <NoBatch />;
  if (batch.recognized_items)
    return (
      <Panel>
        <p>当前测评暂不支持生成或导出分析报告。</p>
        <Button
          type="primary"
          onClick={() => {
            openAssessments(null);
            router.push('/insights');
          }}
        >
          返回学情诊断
        </Button>
      </Panel>
    );
  const key = reportContextKey(batch, conditions);
  return (
    <div className="page-stack reports-page">
      <ReportWorkspace key={key} batch={batch} contextKey={key} />
    </div>
  );
}
