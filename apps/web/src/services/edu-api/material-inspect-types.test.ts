import { describe, expect, it } from 'vitest';
import {
  materialAnalysisLabel,
  materialFieldMeaningLabel,
  materialKindLabel,
  materialRecognitionDto,
  materialRoleLabel,
  materialRoleStateLabel,
} from './material-inspect-types';

const sha = 'b'.repeat(64);

describe('material recognition contract', () => {
  it('decodes structured and answersheet inspect responses', () => {
    const csv = materialRecognitionDto({
      source_id: sha,
      file_name: '合成.bin',
      kind: 'csv',
      supported: true,
      role: 'programming',
      role_state: 'candidate',
      route: 'material_understanding',
      tables: [{ ref: 'F1.T1', rows: 2, columns: 4, complete: false, blocked: false }],
      fields: [{ column: 'F1.T1.C1', meaning: 'student_key', evidence: ['F1.T1.C1'] }],
      pending: [],
      analyses: ['code_evidence', 'score_evidence', 'structure_linkage'],
    });

    expect(csv.route).toBe('material_understanding');
    expect(materialKindLabel(csv.kind)).toBe('CSV 表格');
    expect(materialRoleLabel(csv.role, csv.role_state)).toBe('编程答题 / 测评资料');
    expect(materialRoleStateLabel(csv.role_state)).toBe('候选识别');
    expect(materialFieldMeaningLabel('student_key')).toBe('学号 / 考生标识');
    expect(materialAnalysisLabel('code_evidence')).toBe('代码证据');

    const zip = materialRecognitionDto({
      source_id: sha,
      file_name: null,
      kind: 'zip_html',
      supported: true,
      role: 'programming',
      role_state: 'candidate',
      route: 'assessment_import',
      tables: [{ ref: 'answersheet', rows: 22, columns: 1, complete: true, blocked: false }],
      fields: [],
      pending: [],
      analyses: ['code_evidence', 'test_evidence'],
    });
    expect(zip.route).toBe('assessment_import');
    expect(materialKindLabel(zip.kind)).toBe('编程答题卡压缩包');
  });

  it('rejects malformed routes or empty source_id', () => {
    expect(() =>
      materialRecognitionDto({
        source_id: '',
        file_name: null,
        kind: 'csv',
        supported: true,
        role: null,
        role_state: 'needs_confirmation',
        route: 'material_understanding',
        tables: [],
        fields: [],
        pending: [],
        analyses: [],
      }),
    ).toThrow();
  });
});
