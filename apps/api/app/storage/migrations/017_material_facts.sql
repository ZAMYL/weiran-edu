-- B-02-S2: confirmed material positions become business facts, bound to the same bytes and plan.
CREATE TABLE material_facts (
    fact_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES material_records(material_id),
    data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
    owner_teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    kind TEXT NOT NULL CHECK (kind IN ('score','dimension')),
    participant_key TEXT NOT NULL,
    student_pk TEXT REFERENCES students(student_pk),
    identity_basis TEXT NOT NULL CHECK (identity_basis IN ('student_number','none')),
    problem_key TEXT,
    dimension TEXT,
    score_raw TEXT,
    score_value REAL,
    verdict TEXT,
    dimension_score_raw TEXT,
    dimension_score_value REAL,
    feedback TEXT,
    source_table TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row>0),
    plan_sha256 TEXT NOT NULL CHECK (length(plan_sha256)=64),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256)=64),
    created_at TEXT NOT NULL,
    UNIQUE (material_id, source_table, source_row, kind)
) STRICT;
CREATE INDEX material_facts_material ON material_facts(material_id, source_row, fact_id);
CREATE INDEX material_facts_participant ON material_facts(data_scope_id, participant_key);
