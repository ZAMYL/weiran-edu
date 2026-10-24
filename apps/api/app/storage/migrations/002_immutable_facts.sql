-- Assessment facts and original sources are immutable; revisions append new rows.
CREATE TRIGGER immutable_batches_update
BEFORE UPDATE ON batches BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_batches_delete
BEFORE DELETE ON batches BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_batch_sources_update
BEFORE UPDATE ON batch_sources BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_batch_sources_delete
BEFORE DELETE ON batch_sources BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_batch_students_update
BEFORE UPDATE ON batch_students BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_batch_students_delete
BEFORE DELETE ON batch_students BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_evaluations_update
BEFORE UPDATE ON evaluations BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_evaluations_delete
BEFORE DELETE ON evaluations BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_rubric_snapshots_update
BEFORE UPDATE ON rubric_snapshots BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_rubric_snapshots_delete
BEFORE DELETE ON rubric_snapshots BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_rubric_criteria_update
BEFORE UPDATE ON rubric_criteria BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_rubric_criteria_delete
BEFORE DELETE ON rubric_criteria BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_student_identities_update
BEFORE UPDATE ON student_identities BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_student_identities_delete
BEFORE DELETE ON student_identities BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER committed_import_immutable
BEFORE UPDATE ON imports WHEN OLD.status='committed' BEGIN
    SELECT RAISE(ABORT, 'immutable_committed_import');
END;

