'use client';
import { Button } from 'antd';
import type { AssessmentItem } from '../../services/edu-api/assessment-items';
import { teacherQuestions } from '../../services/edu-api/teacher-ai-guidance';

export default function TeacherQuestionSuggestions({
  items,
  disabled,
  onChoose,
}: {
  items: readonly AssessmentItem[];
  disabled: boolean;
  onChoose: (question: string) => void;
}) {
  const questions = teacherQuestions(items);
  if (!questions.length) return null;
  return (
    <div>
      <div className="batch-actions" role="group" aria-label="快捷提问">
        {questions.map((question) => (
          <Button
            key={question.id}
            size="small"
            disabled={disabled}
            title={question.explanation}
            onClick={() => onChoose(question.question)}
          >
            {question.label}
          </Button>
        ))}
      </div>
      <p className="muted small">
        受控模拟分析；未调用真实 Qwen。选择问题后，点击“开始分析”再发起调查。
      </p>
    </div>
  );
}
