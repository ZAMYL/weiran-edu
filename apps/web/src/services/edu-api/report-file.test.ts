import { afterEach, describe, expect, it, vi } from 'vitest';
import { createEduClient } from './client';
import {
  MAX_REPORT_BYTES,
  readReportFile,
  REPORT_MIME,
  reportFilename,
  saveReportFile,
  xlsxDownloadName,
} from './report-file';
import type { ApiBatch } from './types';

const reference = { resultId: 'synthetic-result', version: 2, format: 'md' as const };
const filename = 'weiran-analysis-synthetic-result-v2.md';
function markdown(body = '# 合成汇总报告\n', headers: Record<string, string> = {}) {
  return new Response(body, {
    headers: {
      'content-type': 'text/markdown; charset=utf-8',
      'content-disposition': `attachment; filename="${filename}"`,
      ...headers,
    },
  });
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});
describe('validated report download', () => {
  it.each([
    ['期末测评 · 学情分析', '期末测评 · 学情分析-v2.xlsx'],
    ['  阶段/期末:测评\n报告.  ', '阶段-期末-测评-报告-v2.xlsx'],
    ['../', '测评报告-v2.xlsx'],
    ['AUX.期末测评', '_AUX.期末测评-v2.xlsx'],
    ['𠮷'.repeat(80), `${'𠮷'.repeat(56)}-v2.xlsx`],
  ])('uses a portable XLSX name for the saved report title', (title, expected) => {
    expect(xlsxDownloadName(title, 2)).toBe(expected);
  });
  it('accepts complete Markdown and XLSX responses with a matching server filename', async () => {
    expect((await readReportFile(markdown(), reference)).filename).toBe(filename);
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0, 0]);
    const xlsx = await readReportFile(
      new Response(bytes, {
        headers: {
          'content-type': REPORT_MIME.xlsx,
          'content-length': String(bytes.length),
          'content-disposition': 'attachment; filename="weiran-analysis-synthetic-result-v2.xlsx"',
        },
      }),
      { ...reference, format: 'xlsx' },
    );
    expect(xlsx.bytes).toEqual(bytes);
  });
  it.each([
    [null],
    ['inline; filename="weiran-analysis-synthetic-result-v2.md"'],
    ['attachment; filename="../private.md"'],
    ['attachment; filename="weiran-analysis-synthetic-result-v1.md"'],
    ['attachment; filename="weiran-analysis-other-v2.md"'],
    ['attachment; filename="weiran-analysis-synthetic-result-v2.xlsx"'],
    ["attachment; filename*=UTF-8''%ZZ"],
  ])('rejects missing, unsafe, or stale attachment names: %s', (disposition) => {
    expect(() => reportFilename(disposition, reference)).toThrow('INVALID_REPORT_FILE');
  });
  it.each([
    ['{"error":{"code":"FORBIDDEN"}}', 'application/json'],
    ['{"error":{"code":"FORBIDDEN"}}', 'text/markdown'],
    ['<html>error</html>', 'text/markdown'],
    ['', 'text/markdown'],
    ['# report\0', 'text/markdown'],
  ])(
    'never accepts a JSON, HTML, empty, or invalid response as a report',
    async (body, contentType) => {
      await expect(
        readReportFile(markdown(body, { 'content-type': contentType }), reference),
      ).rejects.toMatchObject({ code: 'INVALID_REPORT_FILE' });
    },
  );
  it('rejects invalid XLSX signatures, broken UTF-8, and incomplete file bodies', async () => {
    await expect(
      readReportFile(
        markdown('# report', {
          'content-type': REPORT_MIME.xlsx,
          'content-disposition': 'attachment; filename="weiran-analysis-synthetic-result-v2.xlsx"',
        }),
        { ...reference, format: 'xlsx' },
      ),
    ).rejects.toThrow('INVALID_REPORT_FILE');
    await expect(
      readReportFile(
        new Response(new Uint8Array([0xff]), { headers: markdown().headers }),
        reference,
      ),
    ).rejects.toThrow('INVALID_REPORT_FILE');
    await expect(
      readReportFile(markdown('# report', { 'content-length': '100' }), reference),
    ).rejects.toThrow('INVALID_REPORT_FILE');
  });
  it('bounds both declared and streamed bytes and cancels oversized streams', async () => {
    await expect(
      readReportFile(
        markdown('# report', { 'content-length': String(MAX_REPORT_BYTES + 1) }),
        reference,
      ),
    ).rejects.toMatchObject({ status: 413 });
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(MAX_REPORT_BYTES + 1));
      },
      cancel,
    });
    await expect(
      readReportFile(new Response(stream, { headers: markdown().headers }), reference),
    ).rejects.toMatchObject({ status: 413 });
    expect(cancel).toHaveBeenCalledOnce();
  });
  it('removes temporary anchors and revokes object URLs after download', () => {
    vi.useFakeTimers();
    const anchor = { href: '', download: '', hidden: false, click: vi.fn(), remove: vi.fn() };
    const appendChild = vi.fn();
    vi.stubGlobal('document', { createElement: () => anchor, body: { appendChild } });
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:synthetic');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    saveReportFile({ bytes: new Uint8Array([1]), filename, contentType: REPORT_MIME.md });
    expect(anchor.download).toBe(filename);
    expect(appendChild).toHaveBeenCalledWith(anchor);
    expect(anchor.click).toHaveBeenCalledOnce();
    expect(anchor.remove).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(1000);
    expect(revoke).toHaveBeenCalledWith('blob:synthetic');
  });
});

describe('report client session and errors', () => {
  const batch = { batch_id: 'synthetic-batch', snapshot_id: 'synthetic-snapshot' } as ApiBatch;
  it('uses an explicit version, snapshot and format with the existing in-memory session', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(markdown());
    const client = createEduClient(vi.fn(), fetcher);
    client.useTestSession();
    await client.report(batch, reference.resultId, reference.version, 'md');
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe(
      '/api/edu/batches/synthetic-batch/analysis-results/synthetic-result/export?snapshot_id=synthetic-snapshot&expected_version=2&format=md',
    );
    expect(init).toMatchObject({ cache: 'no-store', credentials: 'omit', redirect: 'error' });
    expect(new Headers(init?.headers).has('authorization')).toBe(false);
  });
  it.each([401, 403, 409, 413, 503])(
    'keeps JSON failure %s as an API error and clears only expired sessions',
    async (status) => {
      const unauthorized = vi.fn();
      const client = createEduClient(
        unauthorized,
        vi
          .fn<typeof fetch>()
          .mockResolvedValue(
            Response.json(
              { error: { code: 'SYNTHETIC_FAILURE', message: 'private data' } },
              { status, headers: { 'x-request-id': 'req_synthetic' } },
            ),
          ),
      );
      client.useTestSession();
      await expect(client.report(batch, reference.resultId, 2, 'md')).rejects.toMatchObject({
        status,
        code: 'SYNTHETIC_FAILURE',
        requestId: 'req_synthetic',
      });
      expect(unauthorized).toHaveBeenCalledTimes(status === 401 ? 1 : 0);
    },
  );
  it('fails closed on a successful JSON response and on a late file from a previous session', async () => {
    const client = createEduClient(
      vi.fn(),
      vi.fn<typeof fetch>().mockResolvedValue(Response.json({ error: {} })),
    );
    client.useTestSession();
    await expect(client.report(batch, reference.resultId, 2, 'md')).rejects.toMatchObject({
      status: 502,
      code: 'INVALID_REPORT_FILE',
    });
    let resolve!: (response: Response) => void;
    const slow = createEduClient(
      vi.fn(),
      vi.fn<typeof fetch>().mockImplementation(
        () =>
          new Promise((r) => {
            resolve = r;
          }),
      ),
    );
    slow.useTestSession();
    const pending = expect(slow.report(batch, reference.resultId, 2, 'md')).rejects.toMatchObject({
      name: 'AbortError',
    });
    slow.clear();
    resolve(markdown());
    await pending;
  });
});
