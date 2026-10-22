"""Natural, bounded animation segments. No Qt, bridge reads or task-state writes."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import random
from typing import Callable

from codex_pet.animation import AnimationCatalog, AnimationDef, AnimationPlayer


class BehaviorDirector:
    def __init__(self, catalog: AnimationCatalog, player: AnimationPlayer,
                 rng: random.Random | None = None,
                 clock_ms: Callable[[], float] | None = None):
        self.catalog = catalog
        self.player = player
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock_ms if clock_ms is not None else player.clock_ms
        self.mode: str | None = None
        self.phase = "inactive"
        self.follow_target: int | None = None
        self.last_look: int | None = None
        self.next_look_ms = 0.0
        self.life_actions = True
        self.thinking_actions = True
        self.next_life_ms = float("inf")
        self.next_thinking_ms = float("inf")
        self.last_life = None
        self._half = 0
        self._work_name = "running"
        self._pending: deque[tuple[str, AnimationDef]] = deque()
        self._suspend_revision = player.suspend_revision

    @property
    def active(self) -> bool:
        return self.mode is not None

    def stop(self) -> None:
        self.mode = None
        self.phase = "inactive"
        self._pending.clear()

    def configure_extras(self, life: bool, thinking: bool) -> None:
        if (life, thinking) == (self.life_actions, self.thinking_actions):
            return
        self.life_actions, self.thinking_actions = life, thinking
        if self.active:
            self.ensure(self.mode, restart=True)

    def ensure(self, mode: str, *, restart: bool = False) -> None:
        if mode == self.mode and not restart:
            return
        self.stop()
        self.mode = mode
        self._suspend_revision = self.player.suspend_revision
        if mode == "idle":
            self._half = 0
            self.next_look_ms = self.clock() + self.rng.uniform(20_000, 40_000)
            if self.life_actions and self._life_choices():
                self.next_life_ms = self.clock() + self.rng.uniform(60_000, 120_000)
            self._idle_half()
        elif mode == "running":
            self._work_name = "running"
            if self.thinking_actions and "thinking" in self.catalog.animations:
                self.next_thinking_ms = self.clock() + self.rng.uniform(20_000, 40_000)
            self._work_clip()
        else:
            raise ValueError(f"Unsupported natural behavior: {mode}")

    def tick(self) -> None:
        if not self.active:
            return
        finished = self.player.is_finished()
        if self._suspend_revision != self.player.suspend_revision:
            self.ensure(self.mode, restart=True)
            return
        if not finished:
            return
        if self._pending:
            self._next_segment()
        elif self.mode == "idle":
            if self.phase != "life" and self.life_actions and self._life_choices() and self.clock() >= self.next_life_ms:
                choices = self._life_choices()
                alternatives = [n for n in choices if n != self.last_life]
                self.last_life = self.rng.choice(alternatives or choices)
                self.phase = "life"
                self.player.play(self.last_life, restart=True, cycles=1)
            else:
                if self.phase == "life":
                    self.next_life_ms = self.clock() + self.rng.uniform(60_000, 120_000)
                    self.next_look_ms = self.clock() + self.rng.uniform(20_000, 40_000)
                self._idle_half()
        elif self.phase == "work_clip":
            self.phase = "work_hold"
            source = self.catalog.animations[self._work_name]
            self.player.hold(self._work_name, (source.row, source.columns[-1]),
                             self.rng.uniform(2500, 5500))
        elif self.phase == "work_hold" and self.thinking_actions and "thinking" in self.catalog.animations and self.clock() >= self.next_thinking_ms:
            self.phase = "thinking"
            self.player.play("thinking", restart=True, cycles=1)
        else:
            if self.phase == "thinking":
                self.next_thinking_ms = self.clock() + self.rng.uniform(20_000, 40_000)
            self._work_name = "review" if self._work_name == "running" else "running"
            self._work_clip()

    def _life_choices(self) -> list[str]:
        return [n for n in ("yawn", "groom") if n in self.catalog.animations]

    def _work_clip(self) -> None:
        self.phase = "work_clip"
        self.player.play(self._work_name, restart=True, cycles=self.rng.choice((1, 2)))

    def _next_segment(self) -> None:
        self.phase, definition = self._pending.popleft()
        self.player.play_definition(definition)

    def _idle_half(self) -> None:
        source = self.catalog.animations["idle"]
        if self.catalog.custom_asset_pack:
            # User animations keep their declared frame count and timing.
            self.phase = "idle"
            self.player.play("idle", restart=True, cycles=1)
            return
        start = self._half * 3
        self._half = 1 - self._half
        open_ms = self.rng.uniform(3500, 6500)
        if self.clock() >= self.next_look_ms:
            choices = [i for i in range(len(self.catalog.look_poses)) if i != self.last_look]
            preferred = self.follow_target
            pose = preferred if preferred in choices else self.rng.choice(choices)
            self.last_look = pose
            look_ms = self.rng.uniform(1200, 2200)
            # The glance is part of this open-eye hold, so it cannot postpone a blink indefinitely.
            self._pending.append(("glance_intro", replace(source, columns=(start,),
                                  durations_ms=(280.0,), loop=False)))
            row, column = self.catalog.pose_cell(pose)
            self._pending.append(("glance", replace(source, row=row, columns=(column,),
                                  durations_ms=(look_ms,), loop=False)))
            open_ms -= 280.0 + look_ms
            self.next_look_ms = self.clock() + self.rng.uniform(20_000, 40_000)
        self._pending.append(("idle", replace(source, columns=source.columns[start:start + 3],
                              durations_ms=(open_ms,) + source.durations_ms[start + 1:start + 3],
                              loop=False)))
        self._next_segment()
