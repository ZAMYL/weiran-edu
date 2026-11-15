CREATE TABLE teacher_batch_archive (
    teacher_id TEXT NOT NULL REFERENCES teachers(teacher_id),
    batch_id TEXT NOT NULL REFERENCES batches(batch_id),
    PRIMARY KEY (teacher_id, batch_id)
) STRICT;
