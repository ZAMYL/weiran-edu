// Display-only formatting. Original API exact values remain unchanged.
export function discoverySummaryText(summary: string): string {
  return summary
    .replace(/(-?\d+)\/(\d+)分/g, (original, numerator: string, denominator: string) =>
      Number(denominator) > 0
        ? `${(Number(numerator) / Number(denominator)).toFixed(2)}分`
        : original,
    )
    .replace(
      /Spearman=(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi,
      (_, value: string) => `评分关联系数 ${Number(value).toFixed(2)}`,
    )
    .replace(/(\d+)\/(\d+)条/g, '$2 人中有 $1 人');
}
