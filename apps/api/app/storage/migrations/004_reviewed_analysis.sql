-- Mutable presentation and teacher judgments use append-only versions, not fact updates.
CREATE UNIQUE INDEX batches_snapshot_reference ON batches(batch_id,snapshot_id);
CREATE TABLE text_reviews (
    review_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    target_kind TEXT NOT NULL CHECK (target_kind IN ('comment','rubric')),
    target_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version>0),
    status TEXT NOT NULL CHECK (status IN ('published','withheld')),
    display_text TEXT,
    reviewed_by TEXT NOT NULL REFERENCES teachers(teacher_id),
    created_at TEXT NOT NULL,
    UNIQUE (batch_id,target_kind,target_id,version),
    FOREIGN KEY (batch_id,snapshot_id) REFERENCES batches(batch_id,snapshot_id),
    CHECK ((status='published' AND display_text IS NOT NULL
            AND length(display_text) BETWEEN 1 AND 4000)
        OR (status='withheld' AND display_text IS NULL))
) STRICT;
CREATE TRIGGER review_target_binding BEFORE INSERT ON text_reviews BEGIN
    SELECT CASE WHEN NEW.target_kind='comment' AND NOT EXISTS (
        SELECT 1 FROM evaluations WHERE batch_id=NEW.batch_id AND evidence_id=NEW.target_id
    ) THEN RAISE(ABORT,'invalid_review_target') END;
    SELECT CASE WHEN NEW.target_kind='rubric' AND NOT EXISTS (
        SELECT 1 FROM rubric_criteria r JOIN batches b ON b.rubric_id=r.rubric_id
        WHERE b.batch_id=NEW.batch_id AND r.rubric_criterion_id=NEW.target_id
    ) THEN RAISE(ABORT,'invalid_review_target') END;
END;
CREATE TRIGGER immutable_text_reviews_update BEFORE UPDATE ON text_reviews BEGIN
    SELECT RAISE(ABORT,'immutable_review_history');
END;
CREATE TRIGGER immutable_text_reviews_delete BEFORE DELETE ON text_reviews BEGIN
    SELECT RAISE(ABORT,'immutable_review_history');
END;
CREATE TABLE analysis_results (
    result_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64),
    created_by TEXT NOT NULL REFERENCES teachers(teacher_id),
    created_at TEXT NOT NULL,
    FOREIGN KEY (batch_id,snapshot_id) REFERENCES batches(batch_id,snapshot_id)
) STRICT;
CREATE INDEX analysis_results_batch ON analysis_results(batch_id,created_at,result_id);
CREATE TABLE analysis_result_versions (
    result_id TEXT NOT NULL REFERENCES analysis_results(result_id),
    version INTEGER NOT NULL CHECK (version>0),
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 100),
    teacher_note TEXT NOT NULL CHECK (length(teacher_note)<=2000),
    decisions_json TEXT NOT NULL,
    reviewed_by TEXT NOT NULL REFERENCES teachers(teacher_id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (result_id,version)
) STRICT;
CREATE TRIGGER immutable_analysis_results_update BEFORE UPDATE ON analysis_results BEGIN
    SELECT RAISE(ABORT,'immutable_analysis_result');
END;
CREATE TRIGGER immutable_analysis_results_delete BEFORE DELETE ON analysis_results BEGIN
    SELECT RAISE(ABORT,'immutable_analysis_result');
END;
CREATE TRIGGER immutable_analysis_versions_update BEFORE UPDATE ON analysis_result_versions BEGIN
    SELECT RAISE(ABORT,'immutable_analysis_history');
END;
CREATE TRIGGER immutable_analysis_versions_delete BEFORE DELETE ON analysis_result_versions BEGIN
    SELECT RAISE(ABORT,'immutable_analysis_history');
END;
