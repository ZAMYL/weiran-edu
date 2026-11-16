import type { AssessmentItem } from './assessment-items';
import type { DiscoveryDetail, DiscoverySummary } from './discovery-types';
import type { ExactRational } from './exact';
import type { ItemStatistics } from './types';
import { DIMENSIONS } from './types';

export interface TeacherAnalysis {
  id: string;
  title: string;
  finding: string;
  numbers: string;
  whyItMatters: string;
  nextCheck: string;
  limitation: string;
  state: 'available' | 'needs_detail' | 'insufficient_data';
  source: {
    origin: 'existing_discovery' | 'existing_statistics' | 'verified_profile' | 'student_scores';
    batchId: string;
    snapshotId: string;
    evidenceIds: readonly string[];
    fields: readonly string[];
    factIds?: readonly string[];
  };
}

export interface AnalysisScope {
  batchId: string;
  snapshotId: string;
}

// Formatting only: round the supplied fraction HALF_UP without changing stored statistics.
export function teacherNumber(value: ExactRational): string {
  const n = BigInt(value.numerator);
  const d = BigInt(value.denominator);
  if (d <= BigInt(0)) throw new RangeError('Invalid denominator');
  const absolute = n < BigInt(0) ? -n : n;
  const cents = (absolute * BigInt(200) + d) / (d * BigInt(2));
  const fraction = String(cents % BigInt(100))
    .padStart(2, '0')
    .replace(/0+$/, '');
  return `${n < BigInt(0) && cents !== BigInt(0) ? '-' : ''}${cents / BigInt(100)}${fraction ? '.' + fraction : ''}`;
}

const defaults: readonly AssessmentItem[] = DIMENSIONS.map(({ code, name }) => ({
  item_id: code,
  display_name: name,
  kind: 'competency',
  scale: null,
}));
const operators = { lt: '低于', lte: '不高于', eq: '等于', gte: '不低于', gt: '高于' };
const conditionOperators = {
  '<': '低于',
  '<=': '不高于',
  '==': '等于',
  '>=': '不低于',
  '>': '高于',
};
const itemName = (id: string, items: readonly AssessmentItem[]) =>
  items.find((item) => item.item_id === id)?.display_name ?? `评分项目「${id}」`;
const unitText = (unit?: string) =>
  unit === 'points' ? '分' : unit === 'level' ? '级' : (unit ?? '分');
const itemUnit = (id: string, items: readonly AssessmentItem[]) =>
  unitText(items.find((item) => item.item_id === id)?.scale?.unit);
const source = (
  origin: TeacherAnalysis['source']['origin'],
  scope: AnalysisScope,
  fields: readonly string[],
  evidenceIds: readonly string[] = [],
): TeacherAnalysis['source'] => ({ origin, ...scope, fields, evidenceIds });

/** A summary has no means or conditions. Keep its supplied title until detail is loaded. */
export function describeDiscoverySummary(summary: DiscoverySummary): TeacherAnalysis {
  return {
    id: summary.discovery_id,
    title: summary.title,
    finding: '这条发现的具体评分与人数尚未加载。',
    numbers: '',
    whyItMatters: '',
    nextCheck: '打开详情后查看比较项目、人数和原始评分。',
    limitation: '',
    state: 'needs_detail',
    source: source(
      'existing_discovery',
      { batchId: summary.batch_id, snapshotId: summary.snapshot_id },
      ['title'],
    ),
  };
}

export function describeDiscovery(
  d: DiscoveryDetail,
  items: readonly AssessmentItem[] = defaults,
): TeacherAnalysis {
  const scope = { batchId: d.batch_id, snapshotId: d.snapshot_id };
  const result: TeacherAnalysis = {
    id: d.discovery_id,
    title: '',
    finding: '',
    numbers: '',
    whyItMatters: '',
    nextCheck: '查看参与比较的学生及原始评分，核对具体差别出现在哪里。',
    limitation: '评分差别不能直接说明学生能力、教学原因或评分错误。',
    state: d.status === 'insufficient_evidence' ? 'insufficient_data' : 'available',
    source: source('existing_discovery', scope, [
      'metrics',
      'chart',
      'conditions',
      'status',
      'scope.statistics',
    ]),
  };
  const name = (id: string) => itemName(id, items);
  const conditions = d.conditions
    .map(
      (c) =>
        `${name(c.dimension_code)}评分${operators[c.operator]} ${teacherNumber(c.threshold)} ${itemUnit(c.dimension_code, items)}`,
    )
    .join('，并且');
  switch (d.type) {
    case 'group_difference': {
      const outcome = name(d.metrics.outcome_dimension);
      const unit = itemUnit(d.metrics.outcome_dimension, items);
      const row = d.chart.find((r) => r.dimension_code === d.metrics.outcome_dimension);
      if (!conditions || !row || !row.target.mean || !row.remainder.mean || !row.difference) {
        result.title = `${outcome}：比较数据尚不完整`;
        result.finding = '缺少分组条件或两组有效平均分，暂不描述评分差别。';
        result.state = 'insufficient_data';
        break;
      }
      const direction = BigInt(row.difference.numerator);
      result.title = `${conditions}：${outcome}平均分${direction > BigInt(0) ? '更高' : direction < BigInt(0) ? '更低' : '相同'}`;
      result.finding = `按“${conditions}”分组，两组学生的${outcome}平均分${direction === BigInt(0) ? '相同' : '存在差别'}。`;
      result.numbers = `符合条件的 ${d.metrics.target_count} 人中，${row.target.valid_count} 人的有效评分平均为 ${teacherNumber(row.target.mean)} ${unit}；另外 ${d.metrics.remainder_count} 人中，${row.remainder.valid_count} 人的有效评分平均为 ${teacherNumber(row.remainder.mean)} ${unit}。${d.metrics.unevaluable_count} 人因分组评分不完整或无效，未进入两组比较。`;
      if (row.condition_defined) {
        result.whyItMatters =
          '这项评分本身用于划分两组，当前差别是分组方式的一部分，不能作为另一条新发现。';
      } else {
        const overall = d.scope.statistics.dimensions.find(
          (s) => s.dimension_code === d.metrics.outcome_dimension,
        )?.mean.exact;
        result.whyItMatters =
          overall && direction !== BigInt(0)
            ? `当前分析范围的${outcome}平均分为 ${teacherNumber(overall)} ${unit}，单看这一均分不容易看到上述两组的评分差别。建议对照具体记录，确认差别出现在哪里。`
            : `这条线索指出了需要对照查看的两组评分，便于进一步核查${outcome}的具体记录。`;
      }
      break;
    }
    case 'dimension_relation': {
      const left = name(d.metrics.left),
        right = name(d.metrics.right);
      const rho = d.metrics.rho?.value;
      result.title = `${left}、${right}：${rho === undefined ? '暂不能比较评分关系' : rho > 0 ? '两项评分往往一起较高或较低' : rho < 0 ? '一项评分较高时，另一项往往较低' : '未显示同升同降的趋势'}`;
      result.finding = result.title;
      result.numbers = `本次比较使用 ${d.metrics.paired_count} 名学生的两项有效评分，${d.metrics.excluded_count} 人未参与比较。`;
      const example = d.chart[0];
      if (example)
        result.numbers += `例如，${example.count} 人的${left}评分为 ${teacherNumber(example.x)} 分，${right}评分为 ${teacherNumber(example.y)} 分。`;
      if (rho === undefined) {
        result.state = 'insufficient_data';
      } else {
        result.whyItMatters = `可以对照查看同一名学生在${left}和${right}上的评分，了解评分表现是否一致。`;
      }
      result.limitation = '这里只描述两项评分排序的关系，不能据此判断一项影响了另一项。';
      break;
    }
    case 'rare_combination': {
      const left = name(d.metrics.left),
        right = name(d.metrics.right);
      const boundary = d.metrics.side === 'upper' ? d.metrics.upper_fence : d.metrics.lower_fence;
      result.title = `${left}与${right}：${d.metrics.match_count} 人的分差${d.metrics.side === 'upper' ? '高于' : '低于'} ${teacherNumber(boundary)} 分`;
      result.finding = `有 ${d.metrics.match_count} 名学生的两项评分差落在本次规则划定的范围之外。`;
      result.numbers = `在 ${d.metrics.pair_count} 名两项评分都有效的学生中，${d.metrics.match_count} 人的“${left}评分减${right}评分”${d.metrics.side === 'upper' ? '高于' : '低于'} ${teacherNumber(boundary)} 分；${d.metrics.excluded_count} 人未参与比较。`;
      result.whyItMatters = '这些具体记录可作为逐条核查的起点，先查看原评分及评分要求。';
      result.limitation = '少见的评分组合只是核查线索，不说明评分有误，也不说明学生存在能力缺陷。';
      break;
    }
    case 'boundary_sensitivity': {
      result.title = `${conditions || '当前筛选条件'}：计入边界后增加 ${d.metrics.added_count} 人`;
      result.finding = '是否把恰好达到筛选分数的学生计入，会影响筛选人数。';
      result.numbers = `严格低于边界时有 ${d.metrics.strict_count} 人，计入边界分数后有 ${d.metrics.inclusive_count} 人，增加 ${d.metrics.added_count} 人；${d.metrics.unevaluable_count} 人因评分不完整或无效无法判定。`;
      result.whyItMatters = '如果要据此选择需要查看的学生，先确认边界分数是否应计入。';
      result.nextCheck = '分别查看计入与不计入边界时的学生记录，确认筛选条件符合本次核查目的。';
      result.limitation = '这是筛选人数的变化，不是新增的教学结论。';
      break;
    }
  }
  if (result.state === 'insufficient_data') {
    result.whyItMatters = '当前证据不足以形成稳定描述。';
    result.nextCheck = '先查看有效评分人数和缺少的数据，再决定是否继续比较。';
  }
  return result;
}

/** Keep the supplied order and every finding. No eligibility, ranking or deduplication here. */
export function teacherAnalysisOverview<T>(ordered: readonly T[], limit: 2 | 3 = 3) {
  return {
    highlights: ordered.slice(0, limit),
    remaining: ordered.slice(limit),
    all: ordered.slice(),
  };
}

export type TeacherItemStatistics = Pick<
  ItemStatistics,
  'item_id' | 'valid_count' | 'missing_count' | 'invalid_count' | 'mean'
> & {
  distribution: readonly { score: number; count: number }[];
  evidence_ids?: readonly string[];
};

export function describeItemStatistics(
  stats: TeacherItemStatistics,
  item: AssessmentItem,
  scope: AnalysisScope,
): TeacherAnalysis {
  if (stats.item_id !== item.item_id) throw new Error('Statistics item mismatch');
  const mean = stats.mean.exact;
  const unit = unitText(item.scale?.unit);
  const available = stats.valid_count > 0 && mean !== null;
  const first = stats.distribution[0];
  const last = stats.distribution.at(-1);
  const distribution =
    first && last && first.score !== last.score
      ? `分布中，${first.score} ${unit}有 ${first.count} 人，${last.score} ${unit}有 ${last.count} 人。`
      : first
        ? `分布中，${first.score} ${unit}有 ${first.count} 人。`
        : '';
  return {
    id: item.item_id,
    title: `${item.display_name}：${available ? `${stats.valid_count} 人有有效评分` : '暂没有可用平均分'}`,
    finding: available
      ? `${item.display_name}的有效评分平均为 ${teacherNumber(mean)} ${unit}。`
      : '当前数据不足，暂不描述平均评分。',
    numbers: `有效评分 ${stats.valid_count} 人，缺少评分 ${stats.missing_count} 人，评分无效 ${stats.invalid_count} 人。${distribution}${item.scale ? `本项目量表为 ${item.scale.minimum}～${item.scale.maximum} ${unit}。` : '未提供本项目量表。'}`,
    whyItMatters:
      first && last && first.score !== last.score
        ? '同一平均分可能对应不同的评分分布；上述分数及人数说明，不能把平均分当作每名学生的得分。'
        : '先确认哪些评分实际参与了平均分，避免把缺少评分当作零分。',
    nextCheck: '查看本项目的评分分布及原记录；缺少评分时先核对数据是否齐全。',
    limitation: '平均评分只描述本次测评，不能直接解释为能力水平或教学成效。',
    state: available ? 'available' : 'insufficient_data',
    source: source(
      'existing_statistics',
      scope,
      ['valid_count', 'missing_count', 'invalid_count', 'mean.exact', 'distribution', 'scale'],
      stats.evidence_ids,
    ),
  };
}

export interface TeacherProfileInput {
  profile_id: string;
  batch_id: string;
  snapshot_id: string;
  source_replay_verified: boolean;
  verified_fact_ids: readonly string[];
  conditions: readonly {
    dimension_code: string;
    operator: keyof typeof conditionOperators;
    value: string;
  }[];
  dimensions: readonly {
    dimension_code: string;
    label: string;
    condition_defined: boolean;
    target: { valid_count: number; mean: ExactRational };
    remainder: { valid_count: number; mean: ExactRational };
  }[];
}

export function describeProfile(profile: TeacherProfileInput): TeacherAnalysis[] {
  if (!profile.source_replay_verified) return [];
  const names = new Map(profile.dimensions.map((d) => [d.dimension_code, d.label]));
  const conditions = profile.conditions
    .map(
      (c) =>
        `${names.get(c.dimension_code) ?? c.dimension_code}评分${conditionOperators[c.operator]} ${c.value}`,
    )
    .join('，并且');
  return profile.dimensions.map((d) => ({
    id: `${profile.profile_id}:${d.dimension_code}`,
    title: `${conditions || '已登记群体'}：${d.label}评分对照`,
    finding: `查看该群体与其余学生在${d.label}上的有效评分。`,
    numbers:
      d.target.valid_count > 0 && d.remainder.valid_count > 0
        ? `本组 ${d.target.valid_count} 人的有效评分平均为 ${teacherNumber(d.target.mean)} 分；对照组 ${d.remainder.valid_count} 人的有效评分平均为 ${teacherNumber(d.remainder.mean)} 分。`
        : `本组有效评分 ${d.target.valid_count} 人，对照组有效评分 ${d.remainder.valid_count} 人；暂不比较均分。`,
    whyItMatters: d.condition_defined
      ? '这项评分本身用于分组，组间差别不能作为另一项新发现。'
      : '可据此定位要进一步对照的评分项目和学生记录。',
    nextCheck: `打开${d.label}的评分依据，查看两组各自参与计算的原记录。`,
    limitation: '人数是本项目有效评分人数，不一定等于整组人数；画像不代表学生能力定论。',
    state:
      d.target.valid_count > 0 && d.remainder.valid_count > 0 ? 'available' : 'insufficient_data',
    source: {
      ...source(
        'verified_profile',
        { batchId: profile.batch_id, snapshotId: profile.snapshot_id },
        ['conditions', 'dimensions', 'verified_fact_ids'],
      ),
      factIds: profile.verified_fact_ids.slice(),
    },
  }));
}

export function describeStudentScores(
  student: {
    student_id: string;
    display_label: string;
    scores: readonly { item_id: string; score: ExactRational; evidence_id: string }[];
  },
  items: readonly AssessmentItem[],
  scope: AnalysisScope,
): TeacherAnalysis {
  return {
    id: student.student_id,
    title: `${student.display_label}：本次评分记录`,
    finding: '这里展示各评分项目的原始得分，供逐项核查。',
    numbers: student.scores
      .map(
        (s) =>
          `${itemName(s.item_id, items)} ${teacherNumber(s.score)} ${itemUnit(s.item_id, items)}`,
      )
      .join('；'),
    whyItMatters: '把各项评分放在一起查看，便于确定需要打开哪条原始记录。',
    nextCheck: '点击相应评分查看依据，并结合实际评分要求核查。',
    limitation: '没有提供的评分不补写，也不据此给学生贴能力标签。',
    state: student.scores.length ? 'available' : 'insufficient_data',
    source: source(
      'student_scores',
      scope,
      ['scores'],
      student.scores.map((s) => s.evidence_id),
    ),
  };
}
