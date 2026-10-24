import { create } from 'zustand';
import { DEMO_BATCH } from '../demo/demo';
import { DEFAULT_CONDITIONS, type Batch, type Condition } from '../domain/model';

interface WorkspaceState {
  batches: Batch[];
  batchId: string;
  conditions: Condition[];
  setBatch: (id: string) => void;
  setConditions: (conditions: Condition[]) => void;
  addBatch: (batch: Batch) => void;
}
export const useWorkspace = create<WorkspaceState>((set) => ({
  batches: [DEMO_BATCH],
  batchId: DEMO_BATCH.id,
  conditions: DEFAULT_CONDITIONS,
  setBatch: (batchId) => set({ batchId, conditions: DEFAULT_CONDITIONS.map((c) => ({ ...c })) }),
  setConditions: (conditions) => set({ conditions: conditions.map((c) => ({ ...c })) }),
  addBatch: (batch) =>
    set((state) => {
      if (state.batches.some((b) => b.fingerprint === batch.fingerprint)) return state;
      return { batches: [...state.batches, batch], batchId: batch.id, conditions: [] };
    }),
}));
export function useBatch() {
  return useWorkspace((state) => state.batches.find((b) => b.id === state.batchId)!);
}
