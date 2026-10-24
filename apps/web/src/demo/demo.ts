import {
  DIMENSION_IDS,
  type Batch,
  type DimensionId,
  type Rating,
  type Standard,
  type Student,
} from '../domain/model';

export const DEMO_STANDARD: Standard = {
  id: 'demo-1-5',
  name: '演示标准 · 1–5 分整数',
  version: null,
  dimensions: [
    { id: 'algorithm', name: '算法与数据结构', min: 1, max: 5, step: 1 },
    { id: 'coding', name: '代码实现能力', min: 1, max: 5, step: 1 },
    { id: 'modeling', name: '问题建模能力', min: 1, max: 5, step: 1 },
    { id: 'efficiency', name: '效率意识', min: 1, max: 5, step: 1 },
    { id: 'strategy', name: '解题策略', min: 1, max: 5, step: 1 },
  ],
};
export const DEMO_MATRIX = [
  [3, 7, 18, 12, 5],
  [4, 7, 15, 7, 2],
  [5, 6, 3, 1, 0],
  [4, 2, 1, 0, 0],
  [1, 1, 1, 0, 0],
];
const presets = [
  { number: 8, scores: [2, 4, 3, 4, 5], row: 18 },
  { number: 14, scores: [2, 3, 3, 4, 4], row: 27 },
  { number: 21, scores: [1, 4, 3, 4, 5], row: 34 },
  { number: 27, scores: [2, 3, 2, 3, 4], row: 40 },
];
const comments: Record<DimensionId, string[]> = {
  algorithm: [
    '能够提出解题思路，但算法选择与边界处理仍需加强。',
    '能识别常见数据结构，算法应用仍需结合具体问题检查。',
  ],
  coding: ['代码实现思路清晰，能够独立完成基本功能。', '基本功能能够实现，调试方法还不够系统。'],
  modeling: [
    '能够建立简单的问题模型，复杂约束的表达仍需练习。',
    '能够提取主要条件，需进一步检查模型的完整性。',
  ],
  efficiency: [
    '开始关注运行效率，能够说明部分复杂度差异。',
    '能够比较不同实现的效率，仍需补充边界验证。',
  ],
  strategy: ['能够提出多种解题思路，并说明主要步骤。', '解题思路较完整，能够描述策略选择过程。'],
};
function makeStudent(number: number, scores: number[], row: number): Student {
  return {
    id: `S${String(number).padStart(3, '0')}`,
    sourceId: `demo-${number}`,
    ratings: Object.fromEntries(
      DIMENSION_IDS.map((id, i) => [
        id,
        {
          value: scores[i]!,
          raw: scores[i],
          comment: comments[id][number === 8 ? 0 : number % 2]!,
          source: { file: '评价.xlsx', sheet: 'Sheet1', row, column: String.fromCharCode(66 + i) },
        } satisfies Rating,
      ]),
    ) as Record<DimensionId, Rating>,
  };
}
function createStudents() {
  const remaining = DEMO_MATRIX.map((row) => [...row]);
  const students = presets.map((p) => {
    const row = remaining[5 - p.scores[4]!]!;
    const column = p.scores[0]! - 1;
    row[column] = (row[column] ?? 0) - 1;
    return makeStudent(p.number, p.scores, p.row);
  });
  const used = new Set(presets.map((p) => p.number));
  let number = 1;
  remaining.forEach((row, yi) =>
    row.forEach((count, xi) => {
      for (let n = 0; n < count; n++) {
        while (used.has(number)) number++;
        const sourceRow = 100 + number; // 合成来源，不伪装成已读取真实文件。
        students.push(
          makeStudent(
            number,
            [xi + 1, 2 + (number % 4), 2 + ((number + 1) % 3), 3 + (number % 3), 5 - yi],
            sourceRow,
          ),
        );
        number++;
      }
    }),
  );
  return students.sort((a, b) => a.id.localeCompare(b.id));
}
export const DEMO_BATCH: Batch = {
  id: 'demo-programming-1',
  name: '程序设计 · 第一次测评',
  filename: '评价.xlsx',
  fingerprint: 'synthetic-demo',
  demo: true,
  standard: DEMO_STANDARD,
  students: createStudents(),
};
