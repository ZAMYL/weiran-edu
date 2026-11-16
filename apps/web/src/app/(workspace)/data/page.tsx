import { ModePage } from '../../data-mode';
import LiveData from '../../../features/live/LiveDataPage';

export default function Page() {
  return <ModePage live={<LiveData />} />;
}
