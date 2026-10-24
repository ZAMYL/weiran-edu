import { validateWorkbook } from '../domain/import-validation';
import type { Standard } from '../domain/model';

export async function parseExcel(file: File, standard: Standard) {
  if (!file.name.toLowerCase().endsWith('.xlsx'))
    throw new Error('请选择 .xlsx 格式的 Excel 文件。');
  const buffer = await file.arrayBuffer();
  const fingerprint = await fingerprintBuffer(buffer);
  const { default: readXlsx } = await import('read-excel-file/browser');
  const sheets = await readXlsx(buffer);
  return validateWorkbook(
    sheets.map((s) => ({ name: s.sheet, data: s.data })),
    file.name,
    fingerprint,
    standard,
  );
}
export async function fingerprintBuffer(buffer: ArrayBuffer) {
  const hash = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join('');
}
