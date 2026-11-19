-- 题目 → 五维映射（可配置，默认空 = 不折算）。
CREATE TABLE problem_dimensions (
    problem_id TEXT PRIMARY KEY REFERENCES problems(problem_id),
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    dimension_code TEXT NOT NULL CHECK (
        dimension_code IN (
            'algorithm_data_structure', 'code_implementation', 'problem_modeling',
            'efficiency_awareness', 'solution_strategy'
        )
    ),
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX problem_dimensions_assessment ON problem_dimensions (assessment_id, dimension_code);
