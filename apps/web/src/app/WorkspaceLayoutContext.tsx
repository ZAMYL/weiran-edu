'use client';

import { createContext, useContext } from 'react';

export const WorkspaceLayoutContext = createContext({ fullscreen: false });
export function useWorkspaceLayout() {
  return useContext(WorkspaceLayoutContext);
}
