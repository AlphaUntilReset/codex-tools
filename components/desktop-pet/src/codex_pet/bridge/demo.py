"""本地演示桥：事件全部明确标注 demo，不冒充真实 Codex 任务（验收 C08）。"""

from __future__ import annotations

import itertools
import time

from PySide6.QtCore import QObject, QTimer, Signal

from codex_pet.bridge.interface import (
    Bridge,
    BridgeState,
    Connection,
    TaskSnapshot,
    TaskStatus,
)


class DemoBridge(Bridge):
    name = "demo"

    def __init__(self) -> None:
        super().__init__()
        self._timer: QTimer | None = None
        self._seq = itertools.count(1)
        self._tasks: dict[str, TaskSnapshot] = {}
        self._script = [
            ("demo-task-alpha", TaskStatus.RUNNING),
            ("demo-task-alpha", TaskStatus.RUNNING),
            ("demo-task-alpha", TaskStatus.READY),
            ("demo-task-alpha", TaskStatus.IDLE),
            ("demo-task-beta", TaskStatus.RUNNING),
            ("demo-task-beta", TaskStatus.NEEDS_INPUT),
            ("demo-task-beta", TaskStatus.RUNNING),
            ("demo-task-beta", TaskStatus.BLOCKED),
            ("demo-task-beta", TaskStatus.READY),
            ("demo-task-beta", TaskStatus.IDLE),
        ]
        self._index = 0

    def start(self) -> None:
        self._timer = QTimer()
        self._timer.setInterval(2500)
        self._timer.timeout.connect(self._advance)
        self._timer.start()
        self._emit_state(Connection.CONNECTED)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    def _advance(self) -> None:
        task_id, status = self._script[self._index % len(self._script)]
        self._index += 1
        self._tasks[task_id] = TaskSnapshot(
            task_id=task_id,
            status=status,
            observed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            seq=next(self._seq),
        )
        self._emit_state(Connection.CONNECTED)

    def _emit_state(self, connection: Connection) -> None:
        self.emit(
            BridgeState(
                source="demo",
                mode="demo",
                connection=connection,
                capabilities=("task_status",),
                tasks=dict(self._tasks),
                last_observed_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
        )
