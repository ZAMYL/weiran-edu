import { DIMENSIONS, type ApiEvidence, type ApiRubric } from './types';

// Presentation only: retain individual evidence; never average duplicate scores or fill gaps.
export function studentPortraitRows(items: ApiEvidence[], policy: ApiRubric['score_policy']) {
  return DIMENSIONS.map((dimension) => {
    const evidence = items.filter((item) => item.dimension_code === dimension.code);
    const candidate = evidence.length === 1 ? evidence[0]!.score : null;
    const status: 'missing' | 'multiple' | 'out_of_range' | 'valid' =
      evidence.length === 0
        ? 'missing'
        : evidence.length > 1
          ? 'multiple'
          : candidate !== null &&
              Number.isFinite(candidate) &&
              candidate >= policy.minimum &&
              candidate <= policy.maximum
            ? 'valid'
            : 'out_of_range';
    return { ...dimension, evidence, status, score: status === 'valid' ? candidate : null };
  });
}
