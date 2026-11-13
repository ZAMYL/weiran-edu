"""Operator-approved purpose handoff for the designated synthetic first-use sample."""

from hashlib import sha256

from app.edu.auth import Principal
from app.edu.models import CommitRequest, CommitResult
from app.edu.service import EduService

from .store import TaskStore


class FirstUseImportService(EduService):
    def __init__(
        self, original: EduService, store: TaskStore, teacher_id: str, workbook: bytes
    ) -> None:
        super().__init__(original.database, parser=original.parser, clock=original.clock)
        self.store = store
        self.teacher_id = teacher_id
        self.file_sha256 = sha256(workbook).hexdigest()
        store.provider.require_registered_source(self.file_sha256)

    def commit(self, principal: Principal, import_id: str, request: CommitRequest) -> CommitResult:
        result = super().commit(principal, import_id, request)
        if principal.teacher_id != self.teacher_id:
            return result
        with self.database.transaction() as connection:
            imported = self._owned_import(connection, principal, import_id)
            if imported["file_sha256"] != self.file_sha256:
                return result
            batch = self.store.reader._batch(
                connection, principal, result.batch_id, result.snapshot_id
            )
            if batch["owner_teacher_id"] != principal.teacher_id or (
                batch["file_sha256"] != self.file_sha256
            ):
                return result
        # The formal commit is already durable. A retry can finish this handoff if
        # it was interrupted. grant() validates the source and never renews a grant.
        self.store.grant(principal, result.batch_id, result.snapshot_id)
        return result
