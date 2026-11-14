'use client';
import { Button } from 'antd';
import type { AssessmentItem } from '../../services/edu-api/assessment-items';
import { teacherQuestions } from '../../services/edu-api/teacher-ai-guidance';

export default function TeacherQuestionSuggestions({
  items,
  disabled,
  selected,
  onChoose,
}: {
  items: readonly AssessmentItem[];
  disabled: boolean;
  selected: string;
  onChoose: (question: string) => void;
}) {
  const questions = teacherQuestions(items);
  if (!questions.length) return null;
  return (
    <div>
      <p>你可以从这些问题开始</p>
      <div className="ai-question-suggestions" role="group" aria-label="快捷提问">
        {questions.map((question) => (
          <Button
            key={question.id}
            size="small"
            disabled={disabled}
            type={selected === question.question ? 'primary' : 'default'}
            aria-pressed={selected === question.question}
            title={question.explanation}
            onClick={() => onChoose(question.question)}
          >
            {question.label}
          </Button>
        ))}
      </div>
    </div>
  );
}
