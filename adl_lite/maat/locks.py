"""Deterministic resource-object locks (Sui-style shared/exclusive objects).

Experiment C of the Maat governance validation showed a hybrid topology —
exclusive objects flow lock-free, shared objects serialize per-object —
Pareto-dominates both a global lock and free-for-all execution. This manager
implements that topology with deterministic, auditable semantics:

  - acquire(): non-blocking; exactly one holder per shared resource.
  - Idempotent re-acquire by the same (holder, task_id) pair (execution retry).
  - Every decision is an in-memory verdict suitable for chain audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LockVerdict:
    allowed: bool
    resource: str
    holder: str
    task_id: str
    reason: str = ""
    contention_count: int = 0


@dataclass
class ResourceLockManager:
    """Per-object exclusive locks over the anchor's shared_resources set."""

    shared_resources: set[str] = field(default_factory=set)
    _holders: dict[str, tuple[str, str]] = field(default_factory=dict)  # res -> (holder, task)
    contention: dict[str, int] = field(default_factory=dict)

    def is_shared(self, resource: str | None) -> bool:
        return resource is not None and resource in self.shared_resources

    def acquire(self, resource: str, holder: str, task_id: str) -> LockVerdict:
        if not self.is_shared(resource):
            return LockVerdict(True, resource or "", holder, task_id, "exclusive object: lock-free")
        current = self._holders.get(resource)
        if current is None:
            self._holders[resource] = (holder, task_id)
            return LockVerdict(True, resource, holder, task_id, "acquired")
        cur_holder, cur_task = current
        if cur_holder == holder and cur_task == task_id:
            return LockVerdict(True, resource, holder, task_id, "idempotent re-acquire")
        self.contention[resource] = self.contention.get(resource, 0) + 1
        return LockVerdict(
            False,
            resource,
            holder,
            task_id,
            reason=f"held by {cur_holder} on {cur_task}",
            contention_count=self.contention[resource],
        )

    def release(self, resource: str | None, holder: str) -> None:
        if resource is None:
            return
        current = self._holders.get(resource)
        if current and current[0] == holder:
            del self._holders[resource]

    def release_for_task(self, task_id: str) -> list[str]:
        freed = [
            res for res, (_h, t) in self._holders.items() if t == task_id
        ]
        for res in freed:
            del self._holders[res]
        return freed

    def holder_of(self, resource: str) -> tuple[str, str] | None:
        return self._holders.get(resource)
