"""Bounded completed calculations, never an authorization or source-validation cache."""

from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from threading import Lock
from time import monotonic

from app.analysis.edu.discovery.models import DiscoveryResult

from .auth import Principal
from .discovery_bridge import DiscoveryInput
from .discovery_models import DiscoverySpec

type Key = tuple[str, str, str, str]


@dataclass(frozen=True)
class Completed:
    data: DiscoveryInput
    result: DiscoveryResult
    expires: float


class CompletedDiscoveryReuse:
    def __init__(
        self, *, capacity: int = 8, ttl: float = 300, clock: Callable[[], float] = monotonic
    ) -> None:
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self._entries: OrderedDict[Key, Completed] = OrderedDict()
        self._lock = Lock()

    @staticmethod
    def key(actor: Principal, session: str, data: DiscoveryInput) -> Key:
        return actor.teacher_id, actor.token_hash, session, data.scope.input_key

    @staticmethod
    def calculation_input(data: DiscoveryInput) -> DiscoveryInput:
        # Paging and selected finding affect projection, never the full calculation.
        return replace(
            data,
            spec=DiscoverySpec(
                snapshot_id=data.spec.snapshot_id,
                scope_conditions=data.spec.scope_conditions,
                profile=data.spec.profile,
            ),
        )

    def read(self, actor: Principal, session: str, data: DiscoveryInput) -> DiscoveryResult | None:
        # Caller has already loaded the complete input under current authorization.
        # Equal means every score, record reference, scope and snapshot, not just its ID.
        key = self.key(actor, session, data)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires <= self.clock() or entry.data != self.calculation_input(data):
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return deepcopy(entry.result)

    def remember(
        self, actor: Principal, session: str, data: DiscoveryInput, result: DiscoveryResult
    ) -> None:
        # Only called after the original pre/post source and revocation checks succeed.
        with self._lock:
            self._entries[self.key(actor, session, data)] = Completed(
                deepcopy(self.calculation_input(data)), deepcopy(result), self.clock() + self.ttl
            )
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def discard(self, actor: Principal, session: str) -> None:
        with self._lock:
            for key in tuple(self._entries):
                if key[:3] == (actor.teacher_id, actor.token_hash, session):
                    del self._entries[key]
