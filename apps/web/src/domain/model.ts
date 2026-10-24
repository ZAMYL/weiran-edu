export const DIMENSION_IDS = ['algorithm', 'coding', 'modeling', 'efficiency', 'strategy'] as const;
export type DimensionId = (typeof DIMENSION_IDS)[number];
export interface Dimension {
  id: DimensionId;
  name: string;
  min: number;
  max: number;
  step: number;
}
export interface Standard {
  id: string;
  name: string;
  version: string | null;
  dimensions: Dimension[];
}
export interface Source {
  file: string;
  sheet: string;
  row: number;
  column: string;
}
export interface Rating {
  value: number;
  raw: unknown;
  comment: string;
  source: Source;
}
export interface Student {
  id: string;
  sourceId: string;
  ratings: Record<DimensionId, Rating>;
}
export interface Batch {
  id: string;
  name: string;
  filename: string;
  fingerprint: string;
  demo: boolean;
  standard: Standard;
  students: Student[];
}
export type Operator = '>=' | '<' | '=' | '<=' | '>';
export interface Condition {
  dimension: DimensionId;
  operator: Operator;
  value: number;
}
export const DEFAULT_CONDITIONS: Condition[] = [
  { dimension: 'strategy', operator: '>=', value: 4 },
  { dimension: 'algorithm', operator: '<', value: 3 },
];
export interface RawSheet {
  name: string;
  data: unknown[][];
}
export interface PreviewRow {
  key: string;
  anonymousId: string;
  sourceId: string;
  dimension: string;
  raw: unknown;
  comment: string;
  source: Source;
}
export interface ImportIssue {
  key: string;
  rowKey?: string;
  title: string;
  detail: string;
  source?: Source;
}
export interface ImportPreview {
  filename: string;
  fingerprint: string;
  standard: Standard;
  rows: PreviewRow[];
  issues: ImportIssue[];
  studentCount: number;
  validCount: number;
  students: Student[];
}
