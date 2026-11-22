import { ModePage } from '../../../data-mode';
import { AnswerSheetLibrary } from '../../../../features/live/AnswerSheets';

export default function Page() {
  return <ModePage live={<AnswerSheetLibrary />} />;
}
