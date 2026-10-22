"""桥接统一接口：宠物核心只消费这里定义的状态与事件（施工方案 7.2）。

这些是本项目定义的内部事件，不是官方 API 的返回样例。
source=mode=demo 仅用于演示，必须明确标注，不得冒充真实任务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class Connection(Enum):
    DISABLED = "disabled"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNSUPPORTED = "unsupported"


class TaskStatus(Enum):
    UNKNOWN = "unknown"
    IDLE = "idle"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    READY = "ready"
    COMPLETED = "completed"  # Finished, without a confirmed unread notification.
    BLOCKED = "blocked"


# Match the official pet: unread completion precedes ordinary running work.
AGGREGATE_PRIORITY = [
    TaskStatus.NEEDS_INPUT,
    TaskStatus.BLOCKED,
    TaskStatus.READY,
    TaskStatus.RUNNING,
    TaskStatus.COMPLETED,
    TaskStatus.IDLE,
]


@dataclass(frozen=True)
class TaskSnapshot:
    """单个任务的最近观测。observed_at 为单调递增序号或时间戳字符串。"""

    task_id: str
    status: TaskStatus
    observed_at: str
    seq: int = 0  # 事件序号，用于丢弃乱序/过期更新
    title: str = ""
    turn_id: str = ""
    unread: bool | None = None  # None means the official read-state source is unavailable.
    attention_reason: str = ""

    def newer_than(self, other: Optional["TaskSnapshot"]) -> bool:
        return other is None or self.seq >= other.seq


@dataclass(frozen=True)
class BridgeState:
    source: str  # codex-desktop | demo | none
    mode: str  # live | demo | disabled
    connection: Connection
    capabilities: tuple[str, ...] = ()
    tasks: dict[str, TaskSnapshot] = field(default_factory=dict)
    last_observed_at: str | None = None

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo" or self.source == "demo"

    def aggregate_status(self) -> TaskStatus:
        """多任务聚合；无任务或断连时返回 UNKNOWN/IDLE，不伪造。"""
        if self.connection != Connection.CONNECTED or not self.tasks:
            return TaskStatus.UNKNOWN
        by_priority = {t.status for t in self.tasks.values()}
        for status in AGGREGATE_PRIORITY:
            if status in by_priority:
                return status
        return TaskStatus.UNKNOWN


TaskCallback = Callable[[BridgeState], None]


class Bridge:
    """桥接基类：轮询 start/stop + 状态回调。子类实现真实来源。"""

    name = "none"

    def __init__(self) -> None:
        self._callback: Optional[TaskCallback] = None

    def set_callback(self, callback: TaskCallback) -> None:
        self._callback = callback

    def emit(self, state: BridgeState) -> None:
        if self._callback is not None:
            self._callback(state)

    def start(self) -> None: ...

    def stop(self) -> None: ...


def aggregate_task_updates(
    tasks: dict[str, TaskSnapshot], updates: list[TaskSnapshot]
) -> dict[str, TaskSnapshot]:
    """按任务合并更新；每个任务只保留更新的观测（seq 去重、乱序丢弃）。"""
    merged = dict(tasks)
    for snap in updates:
        if snap.newer_than(merged.get(snap.task_id)):
            merged[snap.task_id] = snap
        # 过期/重复更新被丢弃
    return merged
