import type { AssessmentItem } from './assessment-items';
import type { AiTask } from './ai-task-types';
import { DIMENSIONS } from './types';

export interface TeacherQuestion {
  id: 'priority' | 'relation' | 'group' | 'quality';
  label: string;
  question: string;
  explanation: string;
}

/** This host's task provider admits the registered five-item assessment, not question sheets. */
export function teacherQuestions(items: readonly AssessmentItem[]): TeacherQuestion[] {
  if (
    items.length !== DIMENSIONS.length ||
    !DIMENSIONS.every((d) =>
      items.some((item) => item.item_id === d.code && item.kind === 'competency'),
    )
  )
    return [];
  const left = DIMENSIONS[0].name;
  const right = DIMENSIONS[1].name;
  return [
    {
      id: 'priority',
      label: '先看看哪些评分值得核查',
      question: '有哪些值得关注的问题？',
      explanation: '在本次测评的已有评分中查找值得进一步查看的群体与记录。',
    },
    {
      id: 'relation',
      label: `对照${left}和${right}的评分`,
      question: `比较 ${left}、${right} 的关联`,
      explanation: '查看两项评分是否往往一起较高或较低，不判断因果。',
    },
    {
      id: 'group',
      label: `按${left}评分分组后，看看${right}`,
      question: `比较 ${left}、${right} 的群体差异`,
      explanation: '核查按第一项评分分组后，第二项评分的两组差别。',
    },
    {
      id: 'quality',
      label: '先检查哪些项目缺少评分',
      question: '检查所有项目的缺分',
      explanation: '核对已有评分的完整情况，不补造缺少的数据。',
    },
  ];
}

export function teacherAiError(
  code: string,
): { message: string; action: string; retry: boolean } | null {
  switch (code) {
    case 'AI_GOAL_UNSUPPORTED':
      return {
        message:
          '目前可以核查评分差别、两项评分的对应情况和缺少评分，暂不能回答教学原因或开放式建议。',
        action: '保留你的问题。请选择一条快捷提问，确认后再发起分析。',
        retry: false,
      };
    case 'AI_CAMPAIGN_ALREADY_USED':
      return {
        message: '这份测评的本轮调查已经发起过。',
        action: '查看已有结果；本轮不能重新开始。',
        retry: false,
      };
    case 'AI_PURPOSE_REQUIRED':
    case 'AI_PURPOSE_REVOKED':
    case 'AI_SYNTHETIC_SOURCE_REQUIRED':
      return {
        message: '这份测评当前不能用于 AI 调查。',
        action: '仍可查看已有评分统计与依据；演示调查只能选择服务器登记并授权的合成批次。',
        retry: false,
      };
    default:
      return null;
  }
}

export function teacherAiResult(task: AiTask): {
  title: string;
  explanation: string;
  origin: string;
  canStartAgain: false;
} {
  // The decoded contract only represents http_mock / not_run. Never infer a live model call.
  const origin = '受控模拟分析；未调用真实 Qwen。';
  const common = { origin, canStartAgain: false as const };
  if (task.state === 'queued' || task.state === 'running') {
    return {
      ...common,
      title: task.state === 'queued' ? '调查已排队' : '正在核查评分',
      explanation: '等待服务返回实际结果。',
    };
  }
  if (task.state !== 'completed' || !task.result) {
    return {
      ...common,
      title: '本轮调查未完成',
      explanation: '没有可展示的本轮结论。可继续查看原有评分统计与依据；不要重复发起本轮调查。',
    };
  }
  switch (task.result.stop_reason) {
    case 'no_supported_findings':
      return {
        ...common,
        title: '本轮没有找到符合规则的线索',
        explanation: '这不等于没有问题，也不代表所有学生的评分表现相同。',
      };
    case 'needs_external_evidence':
      return {
        ...common,
        title: '找到了评分线索，原因还需核查',
        explanation: '需要结合实际题目、评分要求和原记录；现有结果不能解释教学原因。',
      };
    case 'goal_satisfied':
      return {
        ...common,
        title: '本轮问题已完成核查',
        explanation: '可查看具体人数、评分项目及来源记录；不能把评分差别直接解释为能力定论。',
      };
    default:
      return {
        ...common,
        title: '本轮已结束，仍有方向未核查',
        explanation: '当前只展示已核查的结果，不代表已检查所有问题。',
      };
  }
}
