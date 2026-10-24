import {
  DIMENSION_IDS,
  type ImportIssue,
  type ImportPreview,
  type PreviewRow,
  type RawSheet,
  type Rating,
  type Standard,
  type Student,
} from './model';

const REQUIRED_HEADERS = ['学生标识', '能力维度', '原始评分', '原始评语'];
const blank = (value: unknown) =>
  value == null || (typeof value === 'string' && value.trim() === '');
export const rawText = (value: unknown): string =>
  value == null || value === '' ? '—' : value instanceof Date ? value.toISOString() : String(value);

export function validateWorkbook(
  sheets: RawSheet[],
  filename: string,
  fingerprint: string,
  standard: Standard,
): ImportPreview {
  const rows: PreviewRow[] = [];
  const issues: ImportIssue[] = [];
  const groups = new Map<string, Student>();
  const seen = new Set<string>();
  const anonymous = new Map<string, string>();
  let validCount = 0;
  const issue = (title: string, detail: string, row?: PreviewRow) =>
    issues.push({
      key: `issue-${issues.length}`,
      rowKey: row?.key,
      title,
      detail,
      source: row?.source,
    });
  for (const sheet of sheets) {
    if (!sheet.data.some((row) => row.some((v) => !blank(v)))) continue;
    const header = sheet.data[0]?.map((v) => String(v ?? '').trim()) ?? [];
    if (REQUIRED_HEADERS.some((h) => header.filter((v) => v === h).length !== 1)) {
      issue(
        '表头不符合约定',
        `工作表“${sheet.name}”首行必须各有一个：${REQUIRED_HEADERS.join('、')}。`,
      );
      continue;
    }
    const studentColumn = header.indexOf('学生标识');
    const dimensionColumn = header.indexOf('能力维度');
    const scoreColumn = header.indexOf('原始评分');
    const commentColumn = header.indexOf('原始评语');
    sheet.data.slice(1).forEach((cells, index) => {
      if (cells.every(blank)) return;
      const sourceId = blank(cells[studentColumn]) ? '' : String(cells[studentColumn]).trim();
      if (sourceId && !anonymous.has(sourceId))
        anonymous.set(sourceId, `S${String(anonymous.size + 1).padStart(3, '0')}`);
      const row: PreviewRow = {
        key: `${sheet.name}:${index + 2}`,
        sourceId,
        anonymousId: anonymous.get(sourceId) ?? '缺失标识',
        dimension: String(cells[dimensionColumn] ?? '').trim(),
        raw: cells[scoreColumn],
        comment: blank(cells[commentColumn]) ? '' : String(cells[commentColumn]),
        source: {
          file: filename,
          sheet: sheet.name,
          row: index + 2,
          column: excelColumn(scoreColumn),
        },
      };
      rows.push(row);
      const before = issues.length;
      if (!sourceId) issue('缺失学生标识', '学生标识不能为空。', row);
      const dimension = standard.dimensions.find((d) => d.name === row.dimension);
      if (!dimension)
        issue('未知能力维度', `“${row.dimension || '空值'}”不在所选评价标准中。`, row);
      if (blank(row.raw)) issue('缺失评分', '原始评分为空，请在源文件中处理后重新上传。', row);
      else if (typeof row.raw !== 'number' || !Number.isFinite(row.raw))
        issue('评分不是数值', '评分单元格须为数值，不会自动转换文本。', row);
      else if (
        dimension &&
        (row.raw < dimension.min ||
          row.raw > dimension.max ||
          Math.abs(
            (row.raw - dimension.min) / dimension.step -
              Math.round((row.raw - dimension.min) / dimension.step),
          ) > 1e-8)
      ) {
        issue(
          '评分超出标准',
          `原值 ${row.raw}；允许 ${dimension.min}–${dimension.max}，步长 ${dimension.step}。`,
          row,
        );
      }
      if (!row.comment.trim()) issue('缺失原始评语', '请补充源文件原始评语，不会自动生成。', row);
      const identity = JSON.stringify([sourceId, dimension?.id ?? row.dimension]);
      if (seen.has(identity))
        issue('重复学生维度记录', '同一学生的同一维度只能有一条记录，包括跨工作表记录。', row);
      seen.add(identity);
      if (sourceId && !groups.has(sourceId))
        groups.set(sourceId, { id: row.anonymousId, sourceId, ratings: {} as Student['ratings'] });
      if (before === issues.length && dimension && typeof row.raw === 'number') {
        groups.get(sourceId)!.ratings[dimension.id] = {
          value: row.raw,
          raw: row.raw,
          comment: row.comment,
          source: row.source,
        } satisfies Rating;
        validCount++;
      }
    });
  }
  if (!rows.length) issue('没有可导入记录', '请检查文件内容及长表模板。');
  groups.forEach((student) => {
    const missing = DIMENSION_IDS.filter(
      (id) =>
        !rows.some(
          (r) =>
            r.sourceId === student.sourceId &&
            standard.dimensions.find((d) => d.name === r.dimension)?.id === id,
        ),
    );
    if (missing.length)
      issue(
        '学生维度不完整',
        `${student.id} 缺少：${missing.map((id) => standard.dimensions.find((d) => d.id === id)?.name).join('、')}。`,
      );
  });
  return {
    filename,
    fingerprint,
    standard,
    rows,
    issues,
    studentCount: groups.size,
    validCount,
    students: issues.length ? [] : [...groups.values()],
  };
}
function excelColumn(index: number): string {
  let result = '';
  for (let n = index + 1; n > 0; n = Math.floor((n - 1) / 26))
    result = String.fromCharCode(65 + ((n - 1) % 26)) + result;
  return result;
}
