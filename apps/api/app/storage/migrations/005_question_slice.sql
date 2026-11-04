CREATE TABLE question_imports (
 import_id TEXT PRIMARY KEY,
 owner_teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
 data_scope_id TEXT NOT NULL REFERENCES data_scopes(data_scope_id),
 title TEXT NOT NULL, course TEXT, assessment_date TEXT,
 preview_version TEXT NOT NULL, expires_at TEXT NOT NULL,
 file_sha256 TEXT NOT NULL, raw_xlsx BLOB NOT NULL,
 committed_batch_id TEXT REFERENCES question_batches(batch_id)
) STRICT;
CREATE TABLE question_batches (
 batch_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE,
 source_import_id TEXT NOT NULL UNIQUE REFERENCES question_imports(import_id),
 UNIQUE(source_import_id, batch_id)
) STRICT;
CREATE TABLE question_items (
 batch_id TEXT NOT NULL REFERENCES question_batches(batch_id),
 item_id TEXT NOT NULL, original_name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind='question'),
 minimum TEXT NOT NULL, maximum TEXT NOT NULL, step TEXT NOT NULL, unit TEXT NOT NULL,
 source_range TEXT NOT NULL,
 PRIMARY KEY(batch_id,item_id)
) STRICT;
CREATE TABLE question_observations (
 evidence_id TEXT NOT NULL UNIQUE,
 batch_id TEXT NOT NULL, participant_id TEXT NOT NULL, item_id TEXT NOT NULL,
 raw_value TEXT, quality_state TEXT NOT NULL CHECK(quality_state IN
 ('valid','missing','invalid_numeric','out_of_range','off_step')),
 numerator TEXT, denominator TEXT, source_cell TEXT NOT NULL,
 PRIMARY KEY(batch_id,participant_id,item_id),
 FOREIGN KEY(batch_id,item_id) REFERENCES question_items(batch_id,item_id)
) STRICT;
CREATE TABLE question_access (
 batch_id TEXT NOT NULL REFERENCES question_batches(batch_id),
 teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
 PRIMARY KEY(batch_id,teacher_id)
) STRICT;
CREATE TRIGGER question_items_no_update BEFORE UPDATE ON question_items
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_FACT'); END;
CREATE TRIGGER question_items_no_delete BEFORE DELETE ON question_items
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_FACT'); END;
CREATE TRIGGER question_observations_no_update BEFORE UPDATE ON question_observations
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_FACT'); END;
CREATE TRIGGER question_observations_no_delete BEFORE DELETE ON question_observations
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_FACT'); END;
CREATE TRIGGER question_batches_no_update BEFORE UPDATE ON question_batches
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_FACT'); END;
CREATE TRIGGER question_batches_no_delete BEFORE DELETE ON question_batches
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_FACT'); END;
CREATE TRIGGER question_sources_no_update BEFORE UPDATE ON question_imports
 WHEN OLD.committed_batch_id IS NOT NULL AND (
 OLD.raw_xlsx IS NOT NEW.raw_xlsx OR OLD.file_sha256 IS NOT NEW.file_sha256 OR
 OLD.owner_teacher_id IS NOT NEW.owner_teacher_id OR OLD.data_scope_id IS NOT NEW.data_scope_id OR
 OLD.title IS NOT NEW.title OR OLD.course IS NOT NEW.course OR
 OLD.assessment_date IS NOT NEW.assessment_date OR OLD.committed_batch_id IS NOT NEW.committed_batch_id)
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SOURCE'); END;
CREATE TRIGGER question_sources_no_delete BEFORE DELETE ON question_imports
 WHEN OLD.committed_batch_id IS NOT NULL
 BEGIN SELECT RAISE(ABORT,'IMMUTABLE_SOURCE'); END;
