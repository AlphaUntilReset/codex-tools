"""默认独立模式：不连接任何来源，不产生任务状态。"""

from __future__ import annotations

from codex_pet.bridge.interface import Bridge, BridgeState, Connection


class DisabledBridge(Bridge):
    name = "disabled"

    def start(self) -> None:
        self.emit(
            BridgeState(
                source="none",
                mode="disabled",
                connection=Connection.DISABLED,
                capabilities=(),
                tasks={},
            )
        )
