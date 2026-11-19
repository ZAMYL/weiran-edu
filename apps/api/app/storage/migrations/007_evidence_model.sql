-- V1.4 证据模型（答题卡类资料）。与既有 batches（五维 Excel）并存，互不影响。
-- 考号（如 S001）不是 12 位学号，因此单独存在 participant 表，不伪造 student_identities。
CREATE TABLE assessments (
    assessment_id TEXT PRIMARY KEY,
    data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
    owner_teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
    course TEXT,
    assessment_date TEXT,
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    parser_version TEXT NOT NULL,
    student_count INTEGER NOT NULL CHECK (student_count > 0),
    problem_count INTEGER NOT NULL CHECK (problem_count > 0),
    submission_count INTEGER NOT NULL CHECK (submission_count > 0),
    observation_count INTEGER NOT NULL CHECK (observation_count >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (data_scope_id, file_sha256)
) STRICT;

CREATE TABLE assessment_participants (
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    student_pk TEXT NOT NULL REFERENCES students(student_pk),
    external_key TEXT NOT NULL CHECK (length(external_key) BETWEEN 1 AND 64),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 100),
    PRIMARY KEY (assessment_id, student_pk),
    UNIQUE (assessment_id, external_key)
) STRICT;

CREATE TABLE problems (
    problem_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 32),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    total_score INTEGER,
    UNIQUE (assessment_id, label)
) STRICT;

CREATE TABLE submissions (
    submission_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
    student_pk TEXT NOT NULL REFERENCES students(student_pk),
    problem_id TEXT NOT NULL REFERENCES problems(problem_id),
    language TEXT,
    code_text TEXT NOT NULL,
    verdict TEXT,
    score_units INTEGER,
    score_total_units INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (assessment_id, student_pk, problem_id)
) STRICT;

CREATE TABLE test_observations (
    observation_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
    test_index INTEGER NOT NULL CHECK (test_index >= 1),
    status TEXT NOT NULL,
    score_units INTEGER,
    duration_ms INTEGER,
    memory_kb INTEGER,
    UNIQUE (submission_id, test_index)
) STRICT;

CREATE INDEX submissions_assessment ON submissions (assessment_id, student_pk);
CREATE INDEX observations_submission ON test_observations (submission_id);
CREATE INDEX participants_lookup ON assessment_participants (external_key);
