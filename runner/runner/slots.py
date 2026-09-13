"""线程安全的 Runner slot 占用状态。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from threading import Lock

from runner.errors import RunnerError

SLOT_TYPES = ("API", "WEB", "PERFORMANCE")


class SlotState:
    """维护 slot 总量与可用量，并在状态变化时通知外部观察者。"""

    def __init__(
        self,
        totals: Mapping[str, int],
        *,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        normalized: dict[str, int] = {}
        for slot_type in SLOT_TYPES:
            value = totals.get(slot_type, 0)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
                raise RunnerError("Runner slot 总量必须在 0 到 10000 之间")
            normalized[slot_type] = value
        self._totals = normalized
        self._available = dict(normalized)
        self._on_change = on_change
        self._lock = Lock()

    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._on_change = callback

    def totals(self) -> dict[str, int]:
        with self._lock:
            return dict(self._totals)

    def available(self) -> dict[str, int]:
        with self._lock:
            return dict(self._available)

    def try_acquire(self, slot_type: str) -> SlotLease | None:
        normalized = self._normalize_type(slot_type)
        with self._lock:
            if self._available[normalized] <= 0:
                return None
            self._available[normalized] -= 1
            callback = self._on_change
        self._notify(callback)
        return SlotLease(self, normalized)

    def _release(self, slot_type: str) -> None:
        with self._lock:
            if self._available[slot_type] >= self._totals[slot_type]:
                raise RunnerError("Runner slot 重复释放")
            self._available[slot_type] += 1
            callback = self._on_change
        self._notify(callback)

    @staticmethod
    def _normalize_type(slot_type: str) -> str:
        normalized = slot_type.upper() if isinstance(slot_type, str) else ""
        if normalized not in SLOT_TYPES:
            raise RunnerError("Runner slot 类型无效")
        return normalized

    @staticmethod
    def _notify(callback: Callable[[], None] | None) -> None:
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # 状态变更不能因心跳提示回调失败而泄漏或阻断执行。
            return


class SlotLease:
    """一次 slot 占用；重复 release 幂等，避免 finally 路径泄漏。"""

    def __init__(self, state: SlotState, slot_type: str) -> None:
        self._state = state
        self.slot_type = slot_type
        self._released = False
        self._lock = Lock()

    @property
    def released(self) -> bool:
        with self._lock:
            return self._released

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._state._release(self.slot_type)

    def __enter__(self) -> SlotLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.release()
