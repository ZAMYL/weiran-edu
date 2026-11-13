import type { Metadata } from 'next';
import TeacherLoginPage from '../../features/auth/TeacherLoginPage';

export const metadata: Metadata = { title: '教师登录｜未然 EDU' };

export default function LoginPage() {
  return <TeacherLoginPage />;
}
