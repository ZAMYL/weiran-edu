'use client';
import { Button } from 'antd';
import type { TeacherQuestion } from '../../services/edu-api/teacher-ai-guidance';

export default function TeacherQuestionSuggestions({
  questions,
  disabled,
  selected,
  onChoose,
}: {
  questions: readonly TeacherQuestion[];
  disabled: boolean;
  selected: string;
  onChoose: (question: string) => void;
}) {
  if (!questions.length) return null;
  return (
    <div className="ai-question-suggestions" role="group" aria-label="快捷提问">
      {questions.map((question) => (
        <Button
          key={question.id}
          htmlType="button"
          disabled={disabled}
          aria-pressed={selected === question.question}
          title={question.explanation}
          onClick={() => onChoose(question.question)}
        >
          {question.label}
        </Button>
      ))}
    </div>
  );
}
