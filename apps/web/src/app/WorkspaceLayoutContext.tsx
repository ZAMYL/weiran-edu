'use client';

import { createContext, useContext } from 'react';

export const WorkspaceLayoutContext = createContext<{
  fullscreen: boolean;
  summaryHost: HTMLElement | null;
}>({ fullscreen: false, summaryHost: null });
export function useWorkspaceLayout() {
  return useContext(WorkspaceLayoutContext);
}
