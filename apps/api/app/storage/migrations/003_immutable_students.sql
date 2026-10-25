-- Anonymous references belong to immutable assessment snapshots too.
CREATE TRIGGER immutable_students_update
BEFORE UPDATE ON students BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
CREATE TRIGGER immutable_students_delete
BEFORE DELETE ON students BEGIN
    SELECT RAISE(ABORT, 'immutable_assessment_fact');
END;
