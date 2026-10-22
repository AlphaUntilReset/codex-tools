"""动作控制器：任务状态、手动动作与动画状态分离（施工方案 5）。

task_state 只来自桥接（真实或明确标注的演示）；手动挥手等不改任务状态。
"""

from __future__ import annotations

from typing import Optional

from codex_pet.animation import AnimationCatalog, AnimationPlayer
from codex_pet.behavior import BehaviorDirector
from codex_pet.bridge import BridgeState, Connection, TaskStatus

# 任务状态 → 待机类动画（非一次性）
TASK_BASE_ANIMATION = {
    TaskStatus.RUNNING: "running",
    TaskStatus.NEEDS_INPUT: "waiting",
    TaskStatus.BLOCKED: "failed",
    TaskStatus.READY: "idle",  # ready 先播一次挥手再回落
    TaskStatus.COMPLETED: "idle",
    TaskStatus.IDLE: "idle",
    TaskStatus.UNKNOWN: "idle",
}

MANUAL_ACTIONS = ("waving", "jumping", "failed", "review", "yawn", "groom", "thinking")


class PetController:
    def __init__(self, catalog: AnimationCatalog, player: AnimationPlayer,
                 *, director: BehaviorDirector | None = None, natural_behavior: bool = True):
        self._catalog = catalog
        self._player = player
        self._task_status: TaskStatus = TaskStatus.UNKNOWN
        self._manual_until_finish = False
        self._celebrating = False  # 完成(running→ready)时挥手一次
        self._dragging = False
        self._last_bridge_state = None
        self.behavior = director if director is not None else BehaviorDirector(catalog, player)
        self.natural_behavior = natural_behavior
        self._motion_active = True
        self._return_to_base()

    def configure_behavior(self, enabled: bool) -> None:
        if enabled != self.natural_behavior:
            self.natural_behavior = enabled
            self._return_to_base()

    def set_motion_active(self, active: bool) -> None:
        was_active = self._motion_active
        self._motion_active = active
        if active and not was_active and self.behavior.active:
            self.behavior.ensure(self.behavior.mode, restart=True)

    @property
    def task_status(self) -> TaskStatus:
        return self._task_status

    @property
    def animation_name(self) -> str:
        return self._player.current_name

    def apply_bridge_state(self, state: BridgeState) -> None:
        """桥接状态 → 任务状态；断连不把过期 running 当实时事实。"""
        previous = self._task_status
        manual_extra = self._manual_until_finish and self._player.current_name in ("yawn", "groom", "thinking")
        prior = self._last_bridge_state
        completed_now = (
            prior is not None and prior.connection is Connection.CONNECTED
            and state.connection is Connection.CONNECTED
            and any(t.status in (TaskStatus.READY, TaskStatus.COMPLETED)
                    and tid in prior.tasks
                    and prior.tasks[tid].status is TaskStatus.RUNNING
                    and prior.tasks[tid].turn_id == t.turn_id
                    for tid, t in state.tasks.items())
        )
        self._last_bridge_state = state
        if state.connection != Connection.CONNECTED:
            self._task_status = TaskStatus.UNKNOWN
        else:
            self._task_status = state.aggregate_status()
        if manual_extra and previous is not self._task_status:
            self._manual_until_finish = False
        if self._dragging:
            return  # 拖动动画优先，松开后回落到最新任务状态
        if (
            previous is TaskStatus.RUNNING
            and self._task_status in (TaskStatus.READY, TaskStatus.COMPLETED)
            and completed_now
            and not self._manual_until_finish
        ):
            self._celebrating = True
            self.behavior.stop()
            self._player.play("waving", restart=True)
        else:
            if self._celebrating and self._task_status in (TaskStatus.READY, TaskStatus.COMPLETED):
                return  # A poll must not truncate the completion wave.
            self._celebrating = False
            if previous is not self._task_status:
                self.behavior.stop()
            self._return_to_base()

    # ---------- 拖动 ----------

    def begin_drag(self) -> None:
        self._dragging = True
        self._manual_until_finish = False
        self._celebrating = False
        self.behavior.stop()
        self._player.play("idle", restart=True)

    def drag_moving(self, animation: str) -> None:
        """拖动位移方向的跑步动画（running-left / running-right）。"""
        if not self._dragging or animation not in ("running-left", "running-right"):
            return
        if self._player.current_name != animation:
            self._player.play(animation)

    def drag_hold(self) -> None:
        """拖住不动时回到待机姿势（任务动画在松手后恢复）。"""
        if self._dragging and self._player.current_name not in ("idle",):
            if self._player.current_name in ("running-left", "running-right"):
                self._player.play("idle")

    def end_drag(self) -> None:
        self._dragging = False
        self._return_to_base()

    def manual_action(self, name: str) -> None:
        """手动演示动作；播完回落，不影响任务状态。"""
        if name not in MANUAL_ACTIONS or name not in self._catalog.animations:
            raise KeyError(f"不支持的手动动作: {name}")
        if not self._motion_active and name in ("yawn", "groom", "thinking"):
            return
        self._manual_until_finish = True
        self._celebrating = False
        self.behavior.stop()
        self._player.play(name, restart=True, cycles=1)

    def on_click_character(self) -> None:
        """单击人物 → 挥手一次。"""
        self.manual_action("waving")

    def tick(self) -> tuple[Optional[tuple[int, int]], bool]:
        """推进播放；返回 (强制静态帧, 是否需要重绘)。

        - 手动/一次性动画播完后回落到任务基础动画。
        - 返回强制静态帧仅用于减少动态/暂停模式。
        """
        if not self._motion_active or self._dragging:
            return None, False
        if self.behavior.active:
            self.behavior.tick()
        elif self._player.is_finished():
            if self._celebrating:
                self._celebrating = False
                self._return_to_base()
            elif self._manual_until_finish:
                self._manual_until_finish = False
                self._return_to_base()
        return None, True

    def _return_to_base(self) -> None:
        if self._manual_until_finish or self._dragging or self._celebrating:
            return  # 手动动作优先，播完再回落
        base = TASK_BASE_ANIMATION[self._task_status]
        if self.natural_behavior and base in ("idle", "running"):
            self.behavior.ensure(base)
        else:
            was_natural = self.behavior.active
            self.behavior.stop()
            self._player.play(base, restart=was_natural)

    def force_reapply(self) -> None:
        self._manual_until_finish = False
        self._celebrating = False
        self._return_to_base()
