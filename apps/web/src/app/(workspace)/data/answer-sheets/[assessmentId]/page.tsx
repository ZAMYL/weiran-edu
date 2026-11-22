import { ModePage } from '../../../../data-mode';
import { AnswerSheetDetails } from '../../../../../features/live/AnswerSheets';

export default async function Page({ params }: { params: Promise<{ assessmentId: string }> }) {
  const { assessmentId } = await params;
  return <ModePage live={<AnswerSheetDetails key={assessmentId} assessmentId={assessmentId} />} />;
}
