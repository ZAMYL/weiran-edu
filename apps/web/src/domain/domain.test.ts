import { readFile } from 'node:fs/promises';
import { describe, expect, it } from 'vitest';
import readXlsx from 'read-excel-file/node';
import { DEMO_BATCH, DEMO_MATRIX, DEMO_STANDARD } from '../demo/demo';
import { DEFAULT_CONDITIONS, type RawSheet } from './model';
import { filterStudents, heatmap, percentage } from './statistics';
import { validateWorkbook } from './import-validation';
import { useWorkspace } from '../app/store';
import { fingerprintBuffer } from '../services/excel';

const header = ['学生标识', '能力维度', '原始评分', '原始评语'];
const makeRows = (): unknown[][] => [
  header,
  ...DEMO_STANDARD.dimensions.map((d, i) => ['学生甲', d.name, i + 1, `原文 ${d.name}`]),
];
const validate = (rows: unknown[][]) =>
  validateWorkbook([{ name: '成绩', data: rows }], '输入.xlsx', 'hash', DEMO_STANDARD);

describe('统计数据一致性', () => {
  it('演示记录完整、唯一且符合指定矩阵', () => {
    expect(DEMO_BATCH.students).toHaveLength(105);
    expect(new Set(DEMO_BATCH.students.map((s) => s.id)).size).toBe(105);
    expect(heatmap(DEMO_BATCH, 'algorithm', 'strategy').cells).toEqual(DEMO_MATRIX);
    expect(DEMO_BATCH.students.every((s) => Object.values(s.ratings).length === 5)).toBe(true);
  });
  it('默认条件与人数、占比及指定学生一致', () => {
    const group = filterStudents(DEMO_BATCH.students, DEFAULT_CONDITIONS);
    expect(group).toHaveLength(21);
    expect(percentage(group.length, DEMO_BATCH.students.length)).toBe('20%');
    expect(group.find((s) => s.id === 'S008')?.ratings.algorithm.value).toBe(2);
    expect(group.find((s) => s.id === 'S008')?.ratings.strategy.value).toBe(5);
  });
  it('无条件包含所有学生，不可能条件返回空组', () => {
    expect(filterStudents(DEMO_BATCH.students, [])).toHaveLength(105);
    expect(
      filterStudents(DEMO_BATCH.students, [{ dimension: 'algorithm', operator: '>', value: 5 }]),
    ).toEqual([]);
    expect(percentage(0, 0)).toBe('0%');
  });
});
describe('导入校验边界', () => {
  it('保留原值、原文与真实来源行列，并匿名显示', () => {
    const result = validate(makeRows());
    expect(result.issues).toEqual([]);
    expect(result.students).toHaveLength(1);
    expect(result.rows[0]!.anonymousId).toBe('S001');
    expect(result.students[0]!.ratings.algorithm).toEqual({
      value: 1,
      raw: 1,
      comment: '原文 算法与数据结构',
      source: { file: '输入.xlsx', sheet: '成绩', row: 2, column: 'C' },
    });
    expect(result.validCount).toBe(5);
  });
  it.each([
    [6, '评分超出标准'],
    [0, '评分超出标准'],
    [2.5, '评分超出标准'],
    [null, '缺失评分'],
    ['', '缺失评分'],
    ['3', '评分不是数值'],
    [Infinity, '评分不是数值'],
  ])('阻断问题原值 %s', (value, title) => {
    const rows = makeRows();
    rows[1]![2] = value;
    const result = validate(rows);
    expect(result.issues.map((i) => i.title)).toContain(title);
    expect(result.rows[0]!.raw).toBe(value);
    expect(result.students).toEqual([]);
  });
  it('评分标准来自传入标准，不固定满分5', () => {
    const standard = {
      ...DEMO_STANDARD,
      dimensions: DEMO_STANDARD.dimensions.map((d) => ({ ...d, max: 10 })),
    };
    const rows = makeRows();
    rows[1]![2] = 8;
    expect(
      validateWorkbook([{ name: '评价', data: rows }], 'a.xlsx', 'h', standard).issues,
    ).toEqual([]);
  });
  it('阻断缺失评语与学生标识、未知维度、学生缺少维度', () => {
    const rows = makeRows();
    rows[1]![0] = null;
    rows[2]![3] = '';
    rows[3]![1] = '不存在';
    rows.pop();
    const titles = validate(rows).issues.map((i) => i.title);
    expect(titles).toContain('缺失学生标识');
    expect(titles).toContain('缺失原始评语');
    expect(titles).toContain('未知能力维度');
    expect(titles).toContain('学生维度不完整');
  });
  it('跨工作表重复学生维度同样阻断', () => {
    const sheets: RawSheet[] = [
      { name: '一', data: makeRows() },
      { name: '二', data: [header, makeRows()[1]!] },
    ];
    const result = validateWorkbook(sheets, 'a.xlsx', 'h', DEMO_STANDARD);
    expect(result.issues.map((i) => i.title)).toContain('重复学生维度记录');
    expect(result.issues[0]!.source?.sheet).toBe('二');
  });
  it('不静默忽略格式错误工作表或完全空文件', () => {
    const sheets = [
      { name: '正确', data: makeRows() },
      { name: '错误', data: [['不合规表头'], ['内容']] },
    ];
    expect(
      validateWorkbook(sheets, 'a.xlsx', 'h', DEMO_STANDARD).issues.map((i) => i.title),
    ).toContain('表头不符合约定');
    expect(validate([]).issues.map((i) => i.title)).toContain('没有可导入记录');
  });
  it('解析实际 xlsx 模板，校验示例恰有两个问题', async () => {
    for (const [filename, errors] of [
      ['评价长表模板.xlsx', 0],
      ['评价_待校验示例.xlsx', 2],
    ] as const) {
      const buffer = await readFile(
        new URL(`../../tests/fixtures/generated/${filename}`, import.meta.url),
      );
      const sheets = await readXlsx(buffer);
      const parsed = validateWorkbook(
        sheets.map((s) => ({ name: s.sheet, data: s.data })),
        filename,
        'hash',
        DEMO_STANDARD,
      );
      expect(parsed.issues).toHaveLength(errors);
      expect(parsed.studentCount).toBe(2);
      expect(parsed.rows).toHaveLength(10);
    }
  });
  it('指纹仅基于文件内容，重命名不影响重复识别', async () => {
    const buffer = new TextEncoder().encode('same file').buffer;
    expect(await fingerprintBuffer(buffer)).toBe(await fingerprintBuffer(buffer.slice(0)));
    expect(await fingerprintBuffer(buffer)).not.toBe(
      await fingerprintBuffer(new TextEncoder().encode('different').buffer),
    );
  });
});
describe('跨页状态', () => {
  it('追加批次不覆盖原批次且不允许同指纹重复添加', () => {
    const original = useWorkspace.getState();
    const batch = { ...DEMO_BATCH, id: 'import-test', fingerprint: 'new-file', demo: false };
    original.addBatch(batch);
    expect(useWorkspace.getState().batches).toHaveLength(2);
    expect(useWorkspace.getState().batchId).toBe(batch.id);
    useWorkspace.getState().addBatch({ ...batch, id: 'renamed' });
    expect(useWorkspace.getState().batches).toHaveLength(2);
    useWorkspace.setState(original);
  });
});
