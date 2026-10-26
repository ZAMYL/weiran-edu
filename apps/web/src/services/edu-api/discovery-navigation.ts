/** Adapt a verified discovery's full scope to the limited two-condition explorer.
 * Do not drop conflicting, repeated, off-grid or third AND predicates.
 * Unrepresentable discoveries remain inspectable using the original records/query.
 */
import type { DiscoveryDetail } from './discovery-types';
import type { ApiCondition } from './types';
import { parseThreshold } from './view-model';

export function explorerConditions(
  scope: ApiCondition[],
  detail: Pick<DiscoveryDetail, 'conditions'>,
): ApiCondition[] | null {
  const merged = scope.map((condition) => ({ ...condition }));
  for (const condition of detail.conditions) {
    const value = parseThreshold(condition.threshold.display);
    if (
      value === null ||
      Math.abs(value - condition.threshold.numerator / condition.threshold.denominator) > 1e-10 ||
      merged.some((current) => current.dimension_code === condition.dimension_code)
    )
      return null;
    merged.push({ dimension_code: condition.dimension_code, operator: condition.operator, value });
  }
  return merged.length <= 2 ? merged : null;
}
