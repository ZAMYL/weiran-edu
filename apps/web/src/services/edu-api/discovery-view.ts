import { scoreDisplay } from './view-model';
import { exactToNumber } from './exact';
const fractionText = (numerator: string, denominator: string): string | null => {
  if (
    numerator.replace(/^-/, '').length > 1024 ||
    denominator.length > 1024 ||
    !/^-?(?:0|[1-9][0-9]*)(?![\s\S])/.test(numerator) ||
    !/^[1-9][0-9]*(?![\s\S])/.test(denominator)
  )
    return null;
  try {
    return scoreDisplay(exactToNumber({ numerator, denominator }));
  } catch {
    return null;
  }
};
// Display-only formatting. Original API exact values and count denominators remain unchanged.
export function discoverySummaryText(summary: string): string {
  return summary
    .replace(
      /Fraction\(\s*(-?\d+)\s*,\s*(\d+)\s*\)/g,
      (original, numerator: string, denominator: string) =>
        fractionText(numerator, denominator) ?? original,
    )
    .replace(
      /(均值|中位数|平均分|四分位数|评分差)(-?\d+)\/(\d+)/g,
      (original, label: string, numerator: string, denominator: string) => {
        const value = fractionText(numerator, denominator);
        return value === null ? original : `${label}${value}`;
      },
    )
    .replace(/(-?\d+)\/(\d+)\s*分/g, (original, numerator: string, denominator: string) => {
      const value = fractionText(numerator, denominator);
      return value === null ? original : `${value}分`;
    })
    .replace(
      /Spearman=(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi,
      (_, value: string) => `评分关联系数 ${scoreDisplay(Number(value))}`,
    )
    .replace(/有效(\d+)\/(\d+)条/g, '有效评分$1条（共$2条）')
    .replace(/(\d+)\/(\d+)条/g, '$2 人中有 $1 人');
}
