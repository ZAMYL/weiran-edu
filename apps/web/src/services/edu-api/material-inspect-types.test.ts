import { describe, expect, it } from 'vitest';
import {
  materialAnalysisLabel,
  materialFieldMeaningLabel,
  materialKindLabel,
  materialRecognitionDto,
  materialRoleLabel,
  materialRoleStateLabel,
} from './material-inspect-types';

const sha = `sha256:${'b'.repeat(64)}`;

describe('material recognition contract (formal /api/v1/edu/materials)', () => {
  it('decodes structured materials and maps fields, pending items, and capabilities', () => {
    const csv = materialRecognitionDto({
      recognition_id: sha,
      coverage: 'unknown',
      authority: 'none',
      materials: [
        {
          file_name: '合成.bin',
          kind: 'csv',
          byte_size: 128,
          source_id: sha,
          status: 'recognized',
          tables: [
            {
              ref: 'F1.T1',
              name: null,
              rows: 2,
              columns: [
                {
                  ref: 'F1.T1.C1',
                  header: '学号',
                  present: 2,
                  types: ['str'],
                  blocked: false,
                },
                {
                  ref: 'F1.T1.C2',
                  header: '得分',
                  present: 2,
                  types: ['int'],
                  blocked: false,
                },
              ],
            },
          ],
          roles: [
            {
              table: 'F1.T1',
              role: 'programming',
              state: 'candidate',
              reasons: ['matched_headers'],
            },
          ],
          fields: [
            {
              column: 'F1.T1.C1',
              header: '学号',
              meaning: 'student_key',
              state: 'candidate',
              reasons: ['exact_header:学号'],
            },
          ],
          pending: ['F1.T1', 'F1.T1.C1'],
          analyses: [
            {
              name: 'problem_score_distribution',
              module: 'AssessmentReader.problem_patterns',
              table: 'F1.T1',
              available: false,
              missing: ['confirmed_role:programming'],
              needs_confirmation: true,
            },
          ],
          warnings: [],
          error: null,
        },
      ],
      archives: [],
      pending: ['F1.T1', 'F1.T1.C1'],
    });

    expect(csv.route).toBe('material_understanding');
    expect(csv.supported).toBe(true);
    expect(csv.tables).toEqual([
      { ref: 'F1.T1', rows: 2, columns: 2, complete: true, blocked: false },
    ]);
    expect(csv.fields[0]?.column).toBe('F1.T1.C1（学号）');
    expect(csv.pending).toHaveLength(2);
    expect(csv.analyses).toEqual(['problem_score_distribution']);
    expect(materialKindLabel(csv.kind)).toBe('CSV 表格');
    expect(materialRoleLabel(csv.role, csv.role_state)).toBe('编程答题 / 测评资料');
    expect(materialRoleStateLabel(csv.role_state)).toBe('候选识别');
    expect(materialFieldMeaningLabel('student_key')).toBe('学号 / 考生标识');
    expect(materialAnalysisLabel('problem_score_distribution')).toBe('题目得分分布');
  });

  it('decodes zip_html archives to assessment_import and marks zip_other/unsupported files', () => {
    const zip = materialRecognitionDto({
      recognition_id: sha,
      coverage: 'unknown',
      authority: 'none',
      materials: [],
      archives: [
        {
          file_name: 'papers.zip',
          kind: 'zip_html',
          route: '/api/v1/edu/assessments',
          note: '含网页答卷的压缩包，交由既有答题卡导入路径处理',
        },
      ],
      pending: [],
    });
    expect(zip.route).toBe('assessment_import');
    expect(zip.supported).toBe(true);
    expect(materialKindLabel(zip.kind)).toBe('编程答题卡压缩包');

    const disguised = materialRecognitionDto({
      recognition_id: sha,
      coverage: 'unknown',
      authority: 'none',
      materials: [
        {
          file_name: 'fake.xlsx',
          kind: 'unknown',
          byte_size: 16,
          source_id: sha,
          status: 'unsupported',
          tables: [],
          roles: [],
          fields: [],
          pending: [],
          analyses: [],
          warnings: [],
          error: 'IMPORT_FILE_UNSUPPORTED',
        },
      ],
      archives: [],
      pending: [],
    });
    expect(disguised.supported).toBe(false);
    expect(disguised.route).toBe('unsupported');
    expect(disguised.role_state).toBe('unsupported');
    expect(disguised.pending[0]?.code).toBe('IMPORT_FILE_UNSUPPORTED');
  });

  it('rejects empty recognition payloads or malformed authority', () => {
    expect(() =>
      materialRecognitionDto({
        recognition_id: sha,
        coverage: 'unknown',
        authority: 'none',
        materials: [],
        archives: [],
        pending: [],
      }),
    ).toThrow();

    expect(() =>
      materialRecognitionDto({
        recognition_id: '',
        coverage: 'unknown',
        authority: 'none',
        materials: [],
        archives: [],
        pending: [],
      }),
    ).toThrow();
  });
});
