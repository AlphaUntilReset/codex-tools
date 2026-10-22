"""动画目录、播放时钟与朝向选择（纯逻辑，不依赖 Qt）。

- 播放用单调时钟推进，按帧时长走表；暂停/挂起只平移时间轴，不补播。
- 朝向按 22.5° 量化，带死区与滞回，避免鼠标微动造成抖动。
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Optional


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0


@dataclass(frozen=True)
class AnimationDef:
    name: str
    row: int
    columns: tuple[int, ...]
    durations_ms: tuple[float, ...]
    loop: bool
    on_finish: str

    @property
    def total_ms(self) -> float:
        return float(sum(self.durations_ms))

    def frame_at(self, elapsed_ms: float) -> tuple[int, int]:
        """按经过时间返回 (row, col)。非循环动画越界时停在末帧。"""
        if elapsed_ms < 0:
            elapsed_ms = 0.0
        if self.loop:
            pos = elapsed_ms % self.total_ms
        else:
            pos = min(elapsed_ms, self.total_ms - 1e-6)
        acc = 0.0
        for col, dur in zip(self.columns, self.durations_ms):
            acc += dur
            if pos < acc:
                return self.row, col
        return self.row, self.columns[-1]


@dataclass(frozen=True)
class LookPose:
    index: int
    angle_degrees: float
    row: int
    column: int


class AnimationCatalog:
    """从 animations.reference.json 装载动画与朝向定义。"""

    def __init__(self, data: dict):
        self.custom_asset_pack = bool(data.get("custom_asset_pack", False))
        self.cell_size = tuple(data.get("cell_size_px", (768, 832)))
        self.animations: dict[str, AnimationDef] = {}
        for name, spec in data["animations"].items():
            self.animations[name] = AnimationDef(
                name=name,
                row=spec["row"],
                columns=tuple(spec["columns"]),
                durations_ms=tuple(float(d) for d in spec["durations_ms"]),
                loop=bool(spec["loop_default"]),
                on_finish=spec["on_finish"],
            )
        self.look_poses: tuple[LookPose, ...] = tuple(
            LookPose(p["index"], p["angle_degrees"], p["row"], p["column"])
            for p in data["look"]["poses"]
        )
        extra = data.get("extra_static_references", [])
        self.extra_static_cells: frozenset[tuple[int, int]] = frozenset(
            (c["row"], c["column"]) for c in extra
        )
        reduced = data.get("static_reduced_motion", {})
        self.static_reduced_cell: tuple[int, int] = (
            reduced.get("row", 0),
            reduced.get("column", 0),
        )

    @classmethod
    def load(cls, path: Path | str) -> "AnimationCatalog":
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    def pose_cell(self, pose_index: int) -> tuple[int, int]:
        p = self.look_poses[pose_index]
        return p.row, p.column


# 挂起/休眠判定阈值：可见状态下两次查询间隔超过该值视为时间轴断裂，
# 只平移时间轴，不快进补播。
SUSPEND_GAP_MS = 2000.0


class AnimationPlayer:
    """单角色播放器：查询驱动，隐藏时显式暂停。"""

    def __init__(self, catalog: AnimationCatalog, clock_ms: Callable[[], float] = monotonic_ms,
                 idle_hold_ms: float = 1500.0):
        self._catalog = catalog
        # Only the two visually verified open-eye holds change. Keep source/catalog intact.
        self._idle = catalog.animations["idle"]
        if not catalog.custom_asset_pack and self._idle.row == 0 and self._idle.columns == (0, 1, 2, 3, 4, 5):
            hold = max(0.0, min(1500.0, idle_hold_ms))
            self._idle = replace(self._idle, durations_ms=tuple(
                d + hold if i in (0, 3) else d
                for i, d in enumerate(self._idle.durations_ms)
            ))
        self.on_animation_changed: Optional[Callable[[], None]] = None
        self._clock = clock_ms
        self._name = "idle"
        self._origin_ms = clock_ms()
        self._last_now = self._origin_ms
        self._paused_since: Optional[float] = None
        self._finished = False
        self._override: Optional[AnimationDef] = None
        self._expected_wake_ms: Optional[float] = None
        self.suspend_revision = 0

    @property
    def clock_ms(self) -> Callable[[], float]:
        return self._clock

    @property
    def current_name(self) -> str:
        return self._name

    def play(self, name: str, restart: bool = False, *, cycles: int | None = None) -> None:
        if name not in self._catalog.animations:
            raise KeyError(f"未知动画: {name}")
        if name == self._name and not restart and self._override is None and cycles is None:
            return
        definition = None
        if cycles is not None:
            if cycles < 1:
                raise ValueError("cycles must be positive")
            source = self._catalog.animations[name]
            definition = replace(source, columns=source.columns * cycles,
                                 durations_ms=source.durations_ms * cycles, loop=False)
        self._start(name, definition)

    def play_definition(self, definition: AnimationDef) -> None:
        """Play a runtime segment without changing the asset catalog."""
        if not definition.columns or len(definition.columns) != len(definition.durations_ms):
            raise ValueError("A segment needs a duration for each frame")
        if any(d <= 0 for d in definition.durations_ms):
            raise ValueError("Frame durations must be positive")
        self._start(definition.name, definition)

    def hold(self, name: str, cell: tuple[int, int], duration_ms: float) -> None:
        self.play_definition(AnimationDef(name, cell[0], (cell[1],),
                                          (duration_ms,), False, "underlying_state"))

    def _start(self, name: str, definition: AnimationDef | None) -> None:
        self._name = name
        self._override = definition
        self._expected_wake_ms = None
        self._origin_ms = self._now_plain()
        if self._paused_since is not None:
            self._paused_since = self._origin_ms
        self._finished = False
        if self.on_animation_changed is not None:
            self.on_animation_changed()

    @property
    def current_definition(self) -> AnimationDef:
        if self._override is not None:
            return self._override
        return self._idle if self._name == "idle" else self._catalog.animations[self._name]

    def _now_plain(self) -> float:
        now = self._clock()
        self._last_now = now
        return now

    def _query_now(self) -> float:
        """可见运行中的查询；大间隔视为挂起，平移时间轴、不补播。"""
        now = self._clock()
        gap = now - self._last_now
        # A deliberate 6-second hold is not a suspend. Only a late wake counts.
        expected = self._expected_wake_ms if self._expected_wake_ms is not None else self._last_now
        if gap > SUSPEND_GAP_MS and now > expected + SUSPEND_GAP_MS:
            self._origin_ms += gap
            self._expected_wake_ms = None
            self.suspend_revision += 1
        self._last_now = now
        return now

    def _elapsed(self) -> float:
        if self._paused_since is not None:
            return max(0.0, self._paused_since - self._origin_ms)
        return max(0.0, self._query_now() - self._origin_ms)

    def current_frame(self) -> tuple[int, int]:
        anim = self.current_definition
        elapsed = self._elapsed()
        frame = anim.frame_at(elapsed)
        if not anim.loop and elapsed >= anim.total_ms:
            self._finished = True
        return frame

    def remaining_ms(self) -> float | None:
        """当前帧剩余时长（循环动画按模回卷计算），供调度下一帧。"""
        anim = self.current_definition
        elapsed = self._elapsed()
        if not anim.loop and elapsed >= anim.total_ms:
            return None  # Finished static poses need no polling/redraw timer.
        pos = elapsed % anim.total_ms
        acc = 0.0
        for dur in anim.durations_ms:
            acc += dur
            if pos < acc:
                remaining = acc - pos
                self._expected_wake_ms = self._last_now + remaining
                return remaining
        return 1.0

    def is_finished(self) -> bool:
        anim = self.current_definition
        if anim.loop:
            return False
        return self._finished or self._elapsed() >= anim.total_ms

    def pause(self) -> None:
        if self._paused_since is None:
            self._paused_since = self._now_plain()

    def resume(self) -> None:
        if self._paused_since is not None:
            now = self._now_plain()
            self._origin_ms += now - self._paused_since
            self._paused_since = None
            self._expected_wake_ms = None


def angle_from_center(dx: float, dy: float) -> float:
    """鼠标相对人物中心的向量 → 顺时针方位角；正上方为 0°，屏幕 y 向下。"""
    deg = math.degrees(math.atan2(dx, -dy))
    return deg % 360.0


def quantize_direction(angle_deg: float, step: float = 22.5, count: int = 16) -> int:
    """最近方向索引；0° 为正上方，顺时针。"""
    return int(round((angle_deg % 360.0) / step)) % count


@dataclass
class DirectionSelector:
    """带死区与滞回的朝向选择器。

    - 死区: 距离小于 deadzone_px 返回 None（回落 idle）。
    - 滞回: 当前朝向的角距未超过 (半步 + hysteresis_deg) 时不切换。
    """

    poses: tuple[LookPose, ...]
    deadzone_px: float = 60.0
    hysteresis_deg: float = 4.0
    _current: Optional[int] = field(default=None, init=False, repr=False)

    def select(self, dx: float, dy: float, distance: float) -> Optional[int]:
        if distance < self.deadzone_px:
            self._current = None
            return None
        angle = angle_from_center(dx, dy)
        nearest = quantize_direction(angle)
        if self._current is None or self._current == nearest:
            self._current = nearest
            return nearest
        # 滞回：偏向维持当前朝向，除非已明显越过边界
        cur_angle = self.poses[self._current].angle_degrees
        diff = abs((angle - cur_angle + 180.0) % 360.0 - 180.0)
        half_step = 360.0 / len(self.poses) / 2.0
        if diff > half_step + self.hysteresis_deg:
            self._current = nearest
            return nearest
        return self._current

    @property
    def current(self) -> Optional[int]:
        return self._current

    def reset(self) -> None:
        self._current = None
