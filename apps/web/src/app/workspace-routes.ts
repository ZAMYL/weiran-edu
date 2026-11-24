export const dashboard = '/dashboard';
export const upload = '/upload';
export const scoreUpload = '/upload/scores';
export const answerUpload = '/upload/answer-sheets';
export const scoreHistory = '/data';
export const answerHistory = '/data/answer-sheets';

const pages = {
  [dashboard]: { title: '仪表盘' },
  [upload]: { title: '上传' },
  [scoreHistory]: { title: '历史测评', group: 'assessments' },
  [answerHistory]: { title: '历史答题卡', group: 'assessments' },
  '/insights': { title: '学情分析', group: 'analysis', batch: true },
  '/explorer': { title: '画像探索', group: 'analysis', batch: true },
  '/reports': { title: '分析报告', batch: true },
  '/students': { title: '学生管理' },
} satisfies Record<string, { title: string; group?: string; batch?: boolean }>;

export const workspacePaths = [...Object.keys(pages), scoreUpload, answerUpload];

export function workspaceTabKey(path: string) {
  return path === scoreUpload || path === answerUpload ? upload : path;
}

export function workspacePage(path: string):
  | {
      title: string;
      group?: string;
      batch?: boolean;
      menuKey: string;
      assessmentId?: string;
    }
  | undefined {
  const tabKey = workspaceTabKey(path);
  if (Object.hasOwn(pages, tabKey)) {
    return { ...pages[tabKey as keyof typeof pages], menuKey: tabKey };
  }
  const detail = path.match(/^\/data\/answer-sheets\/([^/]+)$/);
  if (!detail?.[1]) return;
  try {
    const assessmentId = decodeURIComponent(detail[1]);
    if (assessmentId.includes('/')) return;
    return { title: '答题卡测评详情', group: 'assessments', menuKey: answerHistory, assessmentId };
  } catch {
    return;
  }
}
