import { afterEach, describe, expect, it, vi } from 'vitest';
import { assertSnapshot, createEduClient } from './client';
import { evidenceDto, studentListDto, type ApiBatch, type InsightMetric } from './types';
import { commentStatusText, metricDisplay, parseThreshold } from './view-model';
import { batchItemsDto } from './assessment-items';

const response = (status: number, data: unknown) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json', 'x-request-id': 'req_synthetic' },
  });
const token = 'synthetic-test-credential-not-a-real-token';
afterEach(() => vi.useRealTimers());

describe('assessment item contract', () => {
  const item = { item_id: 'q13', display_name: '第 13 题', kind: 'question', scale: null };
  const stat = { value: null, exact: null, display: '—' };
  const entry = {
    item_id: item.item_id,
    valid_count: 0,
    invalid_count: 0,
    missing_count: 1,
    mean: stat,
    median: stat,
    minimum: stat,
    maximum: stat,
    distribution: [],
  };

  it('requires complete, unique item metadata while allowing unknown scales', () => {
    expect(batchItemsDto({ recognized_items: [item], available_analyses: [] })).toMatchObject({
      recognized_items: [item],
    });
    expect(() => batchItemsDto({ recognized_items: [item] })).toThrow('INVALID_API_RESPONSE');
    expect(() => batchItemsDto({ recognized_items: [item, item], available_analyses: [] })).toThrow(
      'INVALID_API_RESPONSE',
    );
  });

  it.each([
    { snapshot: 'old', items: [entry], status: 409 },
    { snapshot: 'current', items: [entry, entry], status: 502 },
    { snapshot: 'current', items: [{ ...entry, item_id: 'other' }], status: 502 },
    { snapshot: 'current', items: [], status: 502 },
  ])('rejects invalid project results with status $status', async ({ snapshot, items, status }) => {
    const client = createEduClient(
      vi.fn(),
      vi
        .fn<typeof fetch>()
        .mockResolvedValue(
          response(200, { batch_id: 'b', snapshot_id: snapshot, item_statistics: items }),
        ),
    );
    client.authenticate(token);
    await expect(
      client.itemStatistics({
        batch_id: 'b',
        snapshot_id: 'current',
        recognized_items: [item],
      } as ApiBatch),
    ).rejects.toMatchObject({ status });
  });
});

describe('formal API privacy and cancellation', () => {
  it('explains an unregistered synthetic AI source while preserving its request ID', async () => {
    const transport = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(403, { error: { code: 'AI_SYNTHETIC_SOURCE_REQUIRED' } }));
    const client = createEduClient(vi.fn(), transport);
    client.useTestSession();
    await expect(client.me()).rejects.toMatchObject({
      status: 403,
      code: 'AI_SYNTHETIC_SOURCE_REQUIRED',
      message: '当前 AI 演示仅支持本次服务登记的合成样例原件。此测评仍可查看评分统计。',
      requestId: 'req_synthetic',
    });
  });

  it('allows an explicitly selected server test session without sending credentials from the browser', async () => {
    const transport = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(200, { teacher_id: 'test' }));
    const client = createEduClient(vi.fn(), transport);
    client.useTestSession();
    expect(await client.me()).toEqual({ teacher_id: 'test' });
    expect(new Headers(transport.mock.calls[0]![1]?.headers).has('authorization')).toBe(false);
    client.clear();
    await expect(client.me()).rejects.toMatchObject({ status: 401 });
    expect(transport).toHaveBeenCalledTimes(1);
  });
  it('uses only a bearer header and no cache/cookies/URL credentials', async () => {
    const transport = vi
      .fn<typeof fetch>()
      .mockResolvedValue(response(200, { teacher_id: 'teacher_test' }));
    const client = createEduClient(vi.fn(), transport);
    client.authenticate(token);
    expect(await client.me()).toEqual({ teacher_id: 'teacher_test' });
    const [url, init] = transport.mock.calls[0]!;
    expect(url).toBe('/api/edu/me');
    expect(new Headers(init?.headers).get('authorization')).toBe(`Bearer ${token}`);
    expect(init).toMatchObject({ cache: 'no-store', credentials: 'omit', redirect: 'error' });
    client.clear();
    await expect(client.me()).rejects.toMatchObject({ status: 401 });
    expect(transport).toHaveBeenCalledTimes(1);
  });
  it.each([401, 403, 404, 409, 410, 413, 415, 422, 503])(
    'keeps status %s and redacts raw errors',
    async (status) => {
      const unauthorized = vi.fn();
      const client = createEduClient(
        unauthorized,
        vi.fn<typeof fetch>().mockResolvedValue(
          response(status, {
            error: {
              code: status === 409 ? 'SNAPSHOT_MISMATCH' : 'SYNTHETIC_FAILURE',
              message: 'private input must never display',
            },
          }),
        ),
      );
      client.authenticate(token);
      await expect(client.me()).rejects.toMatchObject({ status, requestId: 'req_synthetic' });
      await client.me().catch((error) => expect(error.message).not.toContain('private input'));
      expect(unauthorized).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
    },
  );
  it('discards an old 401 after a replacement session without logging it out', async () => {
    let resolve!: (value: Response) => void;
    const transport = vi
      .fn<typeof fetch>()
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      )
      .mockResolvedValue(response(200, { teacher_id: 'teacher_new' }));
    const unauthorized = vi.fn(),
      client = createEduClient(unauthorized, transport);
    client.authenticate(token);
    const old = client.me();
    const rejected = expect(old).rejects.toMatchObject({ name: 'AbortError' });
    client.authenticate('synthetic-new-session');
    expect(transport.mock.calls[0]![1]?.signal?.aborted).toBe(true);
    resolve(response(401, { error: { code: 'UNAUTHORIZED' } }));
    await rejected;
    expect(unauthorized).not.toHaveBeenCalled();
    expect(await client.me()).toEqual({ teacher_id: 'teacher_new' });
  });
  it('reports network failure and timeout distinctly', async () => {
    const client = createEduClient(
      vi.fn(),
      vi.fn<typeof fetch>().mockRejectedValue(new TypeError('secret host')),
    );
    client.authenticate(token);
    await expect(client.me()).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' });
    vi.useFakeTimers();
    const slow = createEduClient(
      vi.fn(),
      vi
        .fn<typeof fetch>()
        .mockImplementation(
          (_url, init) =>
            new Promise((_resolve, reject) =>
              init?.signal?.addEventListener('abort', () =>
                reject(new DOMException('timeout', 'AbortError')),
              ),
            ),
        ),
    );
    slow.authenticate(token);
    const timed = expect(slow.me()).rejects.toMatchObject({ status: 504, code: 'REQUEST_TIMEOUT' });
    await vi.advanceTimersByTimeAsync(45_001);
    await timed;
  });
  it('fails closed on a response from a different snapshot or malformed contract', async () => {
    expect(() =>
      assertSnapshot({ batch_id: 'b', snapshot_id: 'old' }, {
        batch_id: 'b',
        snapshot_id: 'current',
      } as ApiBatch),
    ).toThrow(
      expect.objectContaining({
        status: 409,
        code: 'SNAPSHOT_MISMATCH',
        message: '这份测评的数据已更新，请刷新测评后重试。',
      }),
    );
    const client = createEduClient(
      vi.fn(),
      vi.fn<typeof fetch>().mockResolvedValue(response(200, { teacher_id: 123 })),
    );
    client.authenticate(token);
    await expect(client.me()).rejects.toMatchObject({ status: 502, code: 'INVALID_API_RESPONSE' });
  });
});
describe('server totals and decimal presentation', () => {
  it('keeps withheld distinct from empty and rejects unreviewed raw comment text', () => {
    const evidence = {
      evidence_id: 'e',
      batch_id: 'b',
      snapshot_id: 's',
      student_id: 'student',
      dimension_code: 'algorithm_data_structure',
      score: 2.75,
      score_raw: '2.75',
      comment: null,
      comment_status: 'withheld',
      comment_review_version: 0,
      source: {
        file_sha256: 'synthetic',
        sheet_name: 'Sheet2',
        row_number: 3,
        dimension_cell: 'B3',
        score_cell: 'C3',
        comment_cell: 'D3',
      },
      rubric_criterion_id: 'r',
    };
    expect(evidenceDto(evidence).comment_status).toBe('withheld');
    expect(evidenceDto({ ...evidence, comment_status: 'empty' }).comment_status).toBe('empty');
    expect(() => evidenceDto({ ...evidence, comment: 'unreviewed synthetic text' })).toThrow();
    expect(
      evidenceDto({
        ...evidence,
        comment_status: 'published',
        comment_review_version: 1,
        comment: 'synthetic reviewed display',
      }).comment,
    ).toBe('synthetic reviewed display');
    expect(() =>
      evidenceDto({ ...evidence, comment_status: 'published', comment_review_version: 1 }),
    ).toThrow();
    expect(() =>
      evidenceDto({
        ...evidence,
        comment_status: 'published',
        comment: 'synthetic reviewed display',
      }),
    ).toThrow();
  });
  it('preserves total_count independently of page size and decimal scores', () => {
    const page = studentListDto({
      batch_id: 'b',
      snapshot_id: 's',
      total_count: 23,
      items: [
        {
          student_id: 's1',
          display_label: 'S001',
          scores: [{ dimension_code: 'algorithm_data_structure', score: 2.75, evidence_id: 'e1' }],
        },
      ],
      page: { offset: 20, limit: 10, returned: 1, has_more: false },
      private_extra: 'stripped',
    });
    expect(page.total_count).toBe(23);
    expect(page.items[0]!.scores[0]!.score).toBe(2.75);
    expect(page).not.toHaveProperty('private_extra');
  });
  it.each(['2.751', '2.7500000000001', 'NaN', '6', '0', '2e0', ''])(
    'rejects %s without rounding',
    (value) => expect(parseThreshold(value)).toBeNull(),
  );
  it.each(['2.75', '2.750', '1', '5'])('accepts exact %s', (value) =>
    expect(parseThreshold(value)).toBe(Number(value)),
  );
  it('uses display values rather than rational denominators or current-page counts', () => {
    const metric = {
      unit: 'score',
      value: { value: 2.75, exact: { numerator: 11, denominator: 4 }, display: '2.75' },
      denominator: 23,
      source_record_count: 23,
    } as InsightMetric;
    expect(metricDisplay(metric)).toBe('2.75');
    expect(
      metricDisplay({ ...metric, unit: 'ratio', value: { ...metric.value, value: 0.25 } }),
    ).toBe('25%');
    expect(commentStatusText('withheld')).toContain('非空');
    expect(commentStatusText('empty')).toContain('为空');
  });
});
