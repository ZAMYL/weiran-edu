import { describe, expect, it, vi } from 'vitest';
import { analysisListDto, analysisResultDto } from './analysis-types';
import { createEduClient } from './client';
import type { ApiBatch } from './types';

const statistics = {
  batch_id: 'batch',
  snapshot_id: 'snapshot',
  rule_version: 'synthetic',
  scope: { kind: 'batch', record_count: 1 },
  display_policy: { digits: 2, rounding: 'HALF_UP' },
  dimensions: [],
  matrix: null,
};
const candidate = {
  candidate_id: 'candidate',
  batch_id: 'batch',
  snapshot_id: 'snapshot',
  candidate_type: 'ability_group',
  dimension_code: null,
  title: '合成候选',
  summary: '合成摘要',
  metrics: [],
  conditions: [],
  denominator: 1,
  rule_id: 'synthetic',
  rule_version: 'synthetic',
  status: 'insufficient_data',
  limitations: [],
  suggested_next_action: '核对证据',
  public_display_allowed: false,
  public_restrictions: [],
};
const summary = {
  result_id: 'result',
  batch_id: 'batch',
  snapshot_id: 'snapshot',
  version: 1,
  title: '合成分析',
  created_at: 'synthetic',
  updated_at: 'synthetic',
};
const saved = () => ({
  ...summary,
  audience: 'authorized_teacher_only',
  note: '',
  reviewed_by: 'teacher',
  notice: '合成说明',
  analysis: {
    query: { snapshot_id: 'snapshot', conditions: [], include_dimensions: true },
    statistics: structuredClone(statistics),
    group_statistics: null as typeof statistics | null,
    total_count: 1,
    matched_count: 0,
    ratio: { value: 0, exact: { numerator: 0, denominator: 1 }, display: '0.00' },
    members: [],
    candidates: [structuredClone(candidate)],
  },
  findings: [
    {
      candidate: structuredClone(candidate),
      teacher_review: { candidate_id: 'candidate', status: 'pending', note: '' },
    },
  ],
});

describe('saved result context checks', () => {
  it('preserves a genuine empty result and pending decision without synthesizing members', () => {
    const result = analysisResultDto(saved());
    expect(result.analysis.matched_count).toBe(0);
    expect(result.analysis.members).toEqual([]);
    expect(result.findings[0]?.teacher_review.status).toBe('pending');
  });
  it.each([
    'root-statistics',
    'group-statistics',
    'candidate',
    'finding',
    'review',
    'duplicate-review',
    'member-count',
    'version',
  ])('rejects mismatched %s instead of mixing contexts', (kind) => {
    const value = saved();
    if (kind === 'root-statistics') value.analysis.statistics.batch_id = 'other';
    if (kind === 'group-statistics')
      value.analysis.group_statistics = { ...statistics, snapshot_id: 'other' };
    if (kind === 'candidate') value.analysis.candidates[0]!.snapshot_id = 'other';
    if (kind === 'finding') value.findings[0]!.candidate.batch_id = 'other';
    if (kind === 'review') value.findings[0]!.teacher_review.candidate_id = 'other';
    if (kind === 'duplicate-review') value.findings.push(structuredClone(value.findings[0]!));
    if (kind === 'member-count') value.analysis.matched_count = 1;
    if (kind === 'version') value.version = 0;
    expect(() => analysisResultDto(value)).toThrow('INVALID_API_RESPONSE');
  });
  it('rejects summaries from a different snapshot in a list page', () => {
    expect(() =>
      analysisListDto({
        batch_id: 'batch',
        snapshot_id: 'snapshot',
        total_count: 1,
        items: [{ ...summary, snapshot_id: 'other' }],
        page: { offset: 0, limit: 10, returned: 1, has_more: false },
      }),
    ).toThrow('INVALID_API_RESPONSE');
  });
  it('posts only query/title/note and patches the explicit expected version', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => Response.json(saved()));
    const client = createEduClient(vi.fn(), fetcher);
    client.useTestSession();
    const batch = { batch_id: 'batch', snapshot_id: 'snapshot' } as ApiBatch;
    const input = {
      snapshot_id: 'snapshot',
      conditions: [],
      include_dimensions: true,
      title: '合成分析',
      note: '',
    };
    await client.createAnalysis(batch, input);
    expect(JSON.parse(fetcher.mock.calls[0]![1]!.body as string)).toEqual(input);
    const patch = {
      snapshot_id: 'snapshot',
      expected_version: 1,
      title: '合成分析',
      note: '',
      decisions: [{ candidate_id: 'candidate', status: 'confirmed' as const, note: '' }],
    };
    await client.patchAnalysis(batch, 'result', patch);
    expect(fetcher.mock.calls[1]![1]?.method).toBe('PATCH');
    expect(JSON.parse(fetcher.mock.calls[1]![1]!.body as string)).toEqual(patch);
  });
});

function formalSaved() {
  const exact = { numerator: 1, denominator: 1, display: '1.0000' };
  const stats = { ...statistics, scope: { kind: 'batch', record_count: 20 } };
  const scope = {
    batch_id: 'batch',
    snapshot_id: 'snapshot',
    input_key: 'a'.repeat(64),
    profile: 'edu.discovery.profile.v1',
    scope_conditions: [],
    batch_record_count: 20,
    scope_record_count: 20,
    eligible_count: 20,
    unevaluable_count: 0,
    statistics: stats,
    public_display_allowed: false,
  };
  const detail = {
    discovery_id: 'a'.repeat(24),
    batch_id: 'batch',
    snapshot_id: 'snapshot',
    input_key: scope.input_key,
    type: 'dimension_relation',
    rule_id: 'relation',
    rule_version: 'synthetic-v1',
    title: '合成关联',
    summary: '合成摘要',
    status: 'interpret_cautiously',
    public_display_allowed: false,
    scope,
    conditions: [],
    limitations: ['不是因果结论'],
    controlled_queries: [{ discovery_id: 'a'.repeat(24), metric_key: 'paired_scores' }],
    parameters: {
      minimum_samples: 5,
      support_target: 20,
      search_budget: 20,
      maximum_findings: 20,
      maximum_students: 2000,
      correlation_threshold: exact,
      mean_difference: exact,
      rare_share: exact,
      minimum_contrast: exact,
    },
    ranking: {
      score: exact,
      effect: exact,
      support_count: 20,
      support: exact,
      completeness: exact,
      is_statistical_confidence: false,
      robustness: { method: 'synthetic', planned: 0, evaluated: 0, passed: 0, value: null },
    },
    metrics: {
      left: 'algorithm_data_structure',
      right: 'solution_strategy',
      rho: { value: 1, method: 'stdlib_float' },
      paired_count: 20,
      excluded_count: 0,
      student_denominator: 20,
    },
    chart: [{ x: exact, y: exact, count: 20 }],
  };
  return {
    ...saved(),
    analysis: {
      ...saved().analysis,
      query: { snapshot_id: 'snapshot', conditions: [], include_dimensions: false },
      statistics: stats,
      total_count: 20,
      matched_count: 20,
      members: Array.from({ length: 20 }, (_, i) => ({
        student_id: `anon-${i}`,
        display_label: `学生${i}`,
        scores: [],
      })),
      candidates: [] as (typeof candidate)[],
      discovery: {
        spec: {
          snapshot_id: 'snapshot',
          profile: 'edu.discovery.profile.v1',
          scope_conditions: [],
        },
        scope,
        findings: [detail],
      },
    },
    findings: [],
    discovery_findings: [
      {
        discovery: structuredClone(detail),
        teacher_review: { candidate_id: detail.discovery_id, status: 'confirmed', note: '' },
      },
    ],
  };
}
it('decodes formal discoveries separately from statistical candidates and keeps confirmed decisions', () => {
  const result = analysisResultDto(formalSaved());
  expect(result.findings).toEqual([]);
  expect(result.discovery_findings[0]?.teacher_review.status).toBe('confirmed');
  expect(result.analysis.members).toHaveLength(20);
});
it('round-trips lossless ranking in both saved discovery copies and rejects mismatched facts', () => {
  const value = formalSaved();
  const exact = {
    numerator: '24840307107174983',
    denominator: '25000000000000000',
    display: '0.9936',
  };
  Object.assign(value.analysis.discovery.findings[0]!.ranking.score, exact);
  Object.assign(value.discovery_findings[0]!.discovery.ranking.score, exact);
  const decoded = analysisResultDto(JSON.parse(JSON.stringify(value)));
  expect(decoded.analysis.discovery?.findings[0]?.ranking.score).toEqual(exact);
  expect(decoded.discovery_findings[0]?.discovery.ranking.score).toEqual(exact);
  expect(JSON.parse(JSON.stringify(decoded))).toEqual(decoded);
  Object.assign(value.discovery_findings[0]!.discovery.ranking.score, {
    numerator: '24840307107174984',
  });
  expect(() => analysisResultDto(value)).toThrow('INVALID_API_RESPONSE');
});
it('rejects unsafe JSON numbers in saved ranking and string ordinary versions', () => {
  for (const kind of ['ranking', 'version']) {
    const value = formalSaved();
    if (kind === 'ranking')
      Object.assign(value.analysis.discovery.findings[0]!.ranking.score, {
        numerator: Number.MAX_SAFE_INTEGER + 1,
      });
    else Object.assign(value, { version: '2' });
    expect(() => analysisResultDto(value)).toThrow('INVALID_API_RESPONSE');
  }
});
it.each(['snapshot', 'scope', 'query', 'review', 'version', 'metrics', 'duplicate', 'legacy'])(
  'rejects inconsistent saved formal discovery %s',
  (kind) => {
    const value = formalSaved();
    if (kind === 'snapshot') value.analysis.discovery.spec.snapshot_id = 'other';
    if (kind === 'scope') value.analysis.discovery.scope.scope_record_count = 19;
    if (kind === 'query') value.analysis.query.include_dimensions = true;
    if (kind === 'review') value.discovery_findings[0]!.teacher_review.candidate_id = 'other';
    if (kind === 'version') value.discovery_findings[0]!.discovery.rule_version = 'other';
    if (kind === 'metrics') value.discovery_findings[0]!.discovery.metrics.paired_count = 19;
    if (kind === 'duplicate')
      value.discovery_findings.push(structuredClone(value.discovery_findings[0]!));
    if (kind === 'legacy') value.analysis.candidates.push(candidate);
    expect(() => analysisResultDto(value)).toThrow('INVALID_API_RESPONSE');
  },
);
it('normalizes absent optional discoveries for older backends without inventing findings', () => {
  expect(analysisResultDto(saved()).discovery_findings).toEqual([]);
});
