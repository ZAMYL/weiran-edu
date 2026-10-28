import { scoreDisplay } from './view-model';
// Display-only formatting. Original API exact values and count denominators remain unchanged.
export function discoverySummaryText(summary: string): string {
  return summary
    .replace(
      /Fraction\(\s*(-?\d+)\s*,\s*(\d+)\s*\)/g,
      (original, numerator: string, denominator: string) =>
        Number(denominator) > 0 ? scoreDisplay(Number(numerator) / Number(denominator)) : original,
    )
    .replace(
      /(均值|中位数|平均分|四分位数|评分差)(-?\d+)\/(\d+)/g,
      (original, label: string, numerator: string, denominator: string) =>
        Number(denominator) > 0
          ? `${label}${scoreDisplay(Number(numerator) / Number(denominator))}`
          : original,
    )
    .replace(/(-?\d+)\/(\d+)\s*分/g, (original, numerator: string, denominator: string) =>
      Number(denominator) > 0
        ? `${scoreDisplay(Number(numerator) / Number(denominator))}分`
        : original,
    )
    .replace(
      /Spearman=(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi,
      (_, value: string) => `评分关联系数 ${scoreDisplay(Number(value))}`,
    )
    .replace(/有效(\d+)\/(\d+)条/g, '有效评分$1条（共$2条）')
    .replace(/(\d+)\/(\d+)条/g, '$2 人中有 $1 人');
}
