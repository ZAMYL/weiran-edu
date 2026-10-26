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
