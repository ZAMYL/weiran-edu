CREATE TABLE teachers (
    teacher_id TEXT PRIMARY KEY,
    auth_issuer TEXT NOT NULL,
    auth_subject TEXT NOT NULL,
    is_active INTEGER NOT NULL CHECK (is_active IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE (auth_issuer, auth_subject)
) STRICT;
CREATE TABLE teacher_tokens (
    token_hash TEXT PRIMARY KEY CHECK (length(token_hash)=64),
    teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    expires_at TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0,1)),
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE data_scopes (
    data_scope_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE scope_members (
    data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
    teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    can_import INTEGER NOT NULL CHECK (can_import IN (0,1)),
    PRIMARY KEY (data_scope_id, teacher_id)
) STRICT;
CREATE TABLE students (
    student_pk TEXT PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
    created_at TEXT NOT NULL,
    UNIQUE (student_pk, data_scope_id)
) STRICT;
CREATE TABLE student_identities (
    student_pk TEXT PRIMARY KEY,
    data_scope_id TEXT NOT NULL,
    student_number TEXT NOT NULL CHECK (
        length(student_number)=12 AND student_number NOT GLOB '*[^0-9]*'
    ),
    student_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (data_scope_id, student_number),
    FOREIGN KEY (student_pk, data_scope_id) REFERENCES students(student_pk, data_scope_id)
) STRICT;
CREATE TABLE rubric_snapshots (
    rubric_id TEXT PRIMARY KEY,
    rubric_version TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE rubric_criteria (
    rubric_criterion_id TEXT PRIMARY KEY,
    rubric_id TEXT NOT NULL REFERENCES rubric_snapshots(rubric_id),
    dimension_code TEXT NOT NULL CHECK (dimension_code IN (
        'algorithm_data_structure','code_implementation','problem_modeling',
        'efficiency_awareness','solution_strategy'
    )),
    title_raw TEXT NOT NULL,
    text_cells_json TEXT NOT NULL,
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256)=64),
    sheet_name TEXT NOT NULL CHECK (sheet_name='Sheet1'),
    source_range TEXT NOT NULL,
    UNIQUE (rubric_id, dimension_code)
) STRICT;
CREATE TABLE imports (
    import_id TEXT PRIMARY KEY,
    owner_teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
    status TEXT NOT NULL CHECK (status IN ('preview_ready','invalid','committed','expired')),
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
    course TEXT,
    assessment_date TEXT,
    revision_of_batch_id TEXT,
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256)=64),
    content_sha256 TEXT CHECK (content_sha256 IS NULL OR length(content_sha256)=64),
    content_hash_version TEXT NOT NULL CHECK (content_hash_version='content.v1'),
    parser_version TEXT NOT NULL,
    rubric_version TEXT,
    preview_version TEXT NOT NULL CHECK (length(preview_version)=64),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    committed_batch_id TEXT,
    CHECK ((status='committed') = (committed_batch_id IS NOT NULL)),
    CHECK (status NOT IN ('preview_ready','committed') OR content_sha256 IS NOT NULL),
    UNIQUE (import_id, data_scope_id, owner_teacher_id),
    FOREIGN KEY (committed_batch_id, data_scope_id) REFERENCES batches(batch_id, data_scope_id),
    FOREIGN KEY (revision_of_batch_id, data_scope_id) REFERENCES batches(batch_id, data_scope_id)
) STRICT;
CREATE TABLE import_payloads (
    import_id TEXT PRIMARY KEY REFERENCES imports(import_id),
    raw_xlsx BLOB NOT NULL,
    preview_json TEXT NOT NULL,
    quality_json TEXT NOT NULL
) STRICT;
CREATE TABLE batches (
    batch_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE,
    data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
    owner_teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    source_import_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    course TEXT,
    assessment_date TEXT,
    rubric_id TEXT NOT NULL UNIQUE REFERENCES rubric_snapshots(rubric_id),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256)=64),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256)=64),
    content_hash_version TEXT NOT NULL CHECK (content_hash_version='content.v1'),
    parser_version TEXT NOT NULL,
    revision_of_batch_id TEXT UNIQUE,
    student_count INTEGER NOT NULL CHECK (student_count > 0),
    evaluation_count INTEGER NOT NULL CHECK (evaluation_count=student_count*5),
    created_at TEXT NOT NULL,
    CHECK (revision_of_batch_id IS NULL OR revision_of_batch_id<>batch_id),
    UNIQUE (batch_id, data_scope_id),
    UNIQUE (batch_id, rubric_id),
    UNIQUE (data_scope_id, file_sha256),
    UNIQUE (data_scope_id, content_hash_version, content_sha256),
    FOREIGN KEY (source_import_id, data_scope_id, owner_teacher_id)
        REFERENCES imports(import_id, data_scope_id, owner_teacher_id),
    FOREIGN KEY (revision_of_batch_id, data_scope_id) REFERENCES batches(batch_id, data_scope_id)
) STRICT;
CREATE TABLE batch_sources (
    batch_id TEXT PRIMARY KEY REFERENCES batches(batch_id),
    raw_xlsx BLOB NOT NULL
) STRICT;
CREATE TABLE batch_access (
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    permission TEXT NOT NULL CHECK (permission='read'),
    granted_at TEXT NOT NULL,
    PRIMARY KEY (batch_id, teacher_id)
) STRICT;
CREATE TABLE batch_students (
    batch_id TEXT NOT NULL,
    student_pk TEXT NOT NULL,
    data_scope_id TEXT NOT NULL,
    display_label TEXT NOT NULL,
    display_order INTEGER NOT NULL CHECK (display_order>0),
    PRIMARY KEY (batch_id, student_pk),
    UNIQUE (batch_id, display_order),
    UNIQUE (batch_id, display_label),
    FOREIGN KEY (batch_id, data_scope_id) REFERENCES batches(batch_id, data_scope_id),
    FOREIGN KEY (student_pk, data_scope_id) REFERENCES students(student_pk, data_scope_id)
) STRICT;
CREATE TABLE evaluations (
    evidence_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    student_pk TEXT NOT NULL,
    rubric_id TEXT NOT NULL,
    dimension_code TEXT NOT NULL,
    score_units INTEGER NOT NULL CHECK (score_units BETWEEN 100 AND 500),
    score_raw TEXT NOT NULL,
    comment_raw TEXT NOT NULL,
    comment_was_missing INTEGER NOT NULL CHECK (comment_was_missing IN (0,1)),
    comment_display TEXT,
    comment_status TEXT NOT NULL CHECK (comment_status IN ('empty','withheld')),
    identity_cell_raw TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL CHECK (sheet_name='Sheet2'),
    source_row INTEGER NOT NULL CHECK (source_row>0),
    dimension_cell TEXT NOT NULL,
    score_cell TEXT NOT NULL,
    comment_cell TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (comment_display IS NULL),
    UNIQUE (batch_id, student_pk, dimension_code),
    FOREIGN KEY (batch_id, student_pk) REFERENCES batch_students(batch_id, student_pk),
    FOREIGN KEY (batch_id, rubric_id) REFERENCES batches(batch_id, rubric_id),
    FOREIGN KEY (rubric_id, dimension_code) REFERENCES rubric_criteria(rubric_id, dimension_code)
) STRICT;
CREATE INDEX imports_owner ON imports(owner_teacher_id, created_at, import_id);
CREATE INDEX imports_expiry ON imports(status, expires_at);
CREATE INDEX batches_owner ON batches(owner_teacher_id, created_at, batch_id);
CREATE INDEX evaluations_filter ON evaluations(batch_id, dimension_code, score_units, student_pk);
