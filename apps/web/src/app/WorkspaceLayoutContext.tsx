'use client';

import { createContext, useContext, type ReactNode } from 'react';

export const WorkspaceLayoutContext = createContext<{
  fullscreen: boolean;
  summaryHost: HTMLElement | null;
  uploadTypeControl?: ReactNode;
}>({ fullscreen: false, summaryHost: null });
export function useWorkspaceLayout() {
  return useContext(WorkspaceLayoutContext);
}
