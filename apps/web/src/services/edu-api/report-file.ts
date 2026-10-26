// Shared browser/proxy checks. A successful status alone is never a downloadable report.
import type { ReportFormat } from './analysis-types';

export const REPORT_MIME = {
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  md: 'text/markdown',
} as const;
export const MAX_REPORT_BYTES = 8 * 1024 * 1024;
export interface ReportReference {
  resultId: string;
  version: number;
  format: ReportFormat;
}
export interface ReportFile {
  bytes: Uint8Array<ArrayBuffer>;
  filename: string;
  contentType: string;
}
export class ReportFileError extends Error {
  constructor(
    readonly status = 502,
    readonly code = 'INVALID_REPORT_FILE',
  ) {
    super(code);
  }
}
export function reportFilename(disposition: string | null, reference: ReportReference): string {
  if (
    !disposition ||
    !/^attachment(?:;|$)/i.test(disposition) ||
    !/^[a-zA-Z0-9_-]{1,128}$/.test(reference.resultId) ||
    !Number.isSafeInteger(reference.version) ||
    reference.version < 1
  )
    throw new ReportFileError();
  const extended = /(?:^|;)\s*filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const ordinary = /(?:^|;)\s*filename="([^"\r\n]+)"(?:;|$)/i.exec(disposition);
  let filename: string;
  try {
    filename = extended ? decodeURIComponent(extended[1]!.trim()) : (ordinary?.[1] ?? '');
  } catch {
    throw new ReportFileError();
  }
  if (
    filename !== `weiran-analysis-${reference.resultId}-v${reference.version}.${reference.format}`
  )
    throw new ReportFileError();
  return filename;
}
export async function readReportFile(
  response: Response,
  reference: ReportReference,
): Promise<ReportFile> {
  const contentType = response.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase();
  if (response.status !== 200 || contentType !== REPORT_MIME[reference.format])
    throw new ReportFileError();
  const filename = reportFilename(response.headers.get('content-disposition'), reference);
  const length = response.headers.get('content-length');
  if (length !== null && !/^\d+$/.test(length)) throw new ReportFileError();
  if (length !== null && Number(length) > MAX_REPORT_BYTES)
    throw new ReportFileError(413, 'EXPORT_TOO_LARGE');
  if (!response.body) throw new ReportFileError();
  const reader = response.body.getReader();
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > MAX_REPORT_BYTES) {
        await reader.cancel();
        throw new ReportFileError(413, 'EXPORT_TOO_LARGE');
      }
      chunks.push(part.value);
    }
  } finally {
    reader.releaseLock();
  }
  if (!size || (length !== null && Number(length) !== size)) throw new ReportFileError();
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  if (reference.format === 'xlsx') {
    if (bytes[0] !== 0x50 || bytes[1] !== 0x4b || bytes[2] !== 0x03 || bytes[3] !== 0x04)
      throw new ReportFileError();
  } else {
    let text: string;
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      throw new ReportFileError();
    }
    if (!/^#{1,6}\s/.test(text.trimStart()) || text.includes('\0')) throw new ReportFileError();
  }
  return { bytes, filename, contentType };
}
export function saveReportFile(file: ReportFile) {
  const url = URL.createObjectURL(new Blob([file.bytes], { type: file.contentType }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = file.filename;
  anchor.hidden = true;
  try {
    document.body.appendChild(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
