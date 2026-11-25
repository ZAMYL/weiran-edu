'use client';
import { tokens } from '../../app/theme';
import { Button, Flex } from 'antd';
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
    <Flex
      wrap
      gap={tokens.sizeXS}
      className="ai-question-suggestions"
      role="group"
      aria-label="快捷提问"
    >
      {questions.map((question) => {
        const isSelected = selected === question.question;
        return (
          <Button
            key={question.id}
            htmlType="button"
            color={isSelected ? 'primary' : 'default'}
            variant={isSelected ? 'filled' : 'outlined'}
            disabled={disabled}
            aria-pressed={isSelected}
            title={question.explanation}
            onClick={() => onChoose(question.question)}
          >
            {question.label}
          </Button>
        );
      })}
    </Flex>
  );
}
