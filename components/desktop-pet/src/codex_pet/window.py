"""透明无边框宠物窗口：绘制、拖动、缩放、朝向跟随、穿透与 Esc。

窗口为 Qt.Tool（不进任务栏）+ 无边框 + 半透明；程序性显示不抢焦点
（WA_ShowWithoutActivization），点击时才获得交互焦点。
动画与朝向轮询的启停统一由 _update_timers 管理：隐藏/暂停/减少动态时停表。
"""

from __future__ import annotations

import logging
import math
import os
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QWidget

from codex_pet.animation import AnimationCatalog, AnimationPlayer, DirectionSelector
from codex_pet.controller import PetController
from codex_pet.renderer import FrameRenderer
from codex_pet.settings import (
    MAX_CANVAS_HEIGHT_DIP,
    MIN_CANVAS_HEIGHT_DIP,
    Settings,
)

log = logging.getLogger("codex_pet.window")

DRAG_THRESHOLD_DIP = 6.0
DRAG_DIRECTION_DIP = 6.0  # 累计水平位移超过该值切换跑步方向动画
ALPHA_HIT_THRESHOLD = 20  # 源帧 alpha 命中阈值（0–255）
FOLLOW_POLL_MS = 120
HOVER_POLL_MS = 40
HOVER_OPACITY = 0.25
SAVE_DEBOUNCE_MS = 800
WHEEL_STEP_DIP = 24


class PetWindow(QWidget):
    settings_changed = Signal(object)  # Settings（已变更，需持久化）
    context_menu_requested = Signal(object)

    def __init__(
        self,
        catalog: AnimationCatalog,
        player: AnimationPlayer,
        controller: PetController,
        renderer: FrameRenderer,
        settings: Settings,
    ):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self._catalog = catalog
        self._player = player
        self._controller = controller
        self._renderer = renderer
        self._source_w, self._source_h = catalog.cell_size
        self._settings = settings
        self._selector = DirectionSelector(catalog.look_poses)
        self._pose_cell: Optional[tuple[int, int]] = None
        self._drag_active = False
        self._drag_exceeded = False
        self._drag_press_global: Optional[QPoint] = None
        self._drag_offset = QPoint()
        self._drag_origin_pos = QPoint()
        self._drag_last_global: Optional[QPoint] = None
        self._drag_dx_accum = 0.0
        self._drag_hold_timer = QTimer(self, singleShot=True, interval=700)
        self._drag_hold_timer.timeout.connect(self._on_drag_hold)
        self._cached_dpr = 0.0
        self._screen_signal_handle = None  # 已连接 screenChanged 的窗口句柄（showEvent 每次显隐触发，防重复连接）
        self._last_good_pixmap: QPixmap | None = None  # 帧缓存运行期损坏时的绘制兜底
        self._paint_error_logged = False
        self._hover_active = False
        self._hover_companion = None
        self._native_input_transparency = None
        self._input_error_logged = False

        # Native input transparency stops Qt enter/leave events. Keep an independent
        # cursor poll alive even when animations and gaze following are disabled.
        self._hover_timer = QTimer(self, interval=HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._poll_hover)

        self._frame_timer = QTimer(self, singleShot=True)
        self._frame_timer.timeout.connect(self._on_frame_tick)
        self._follow_timer = QTimer(self, interval=FOLLOW_POLL_MS)
        self._follow_timer.timeout.connect(self._poll_follow)
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_position)
        self._player.on_animation_changed = self._on_animation_changed

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        w, h = self._logical_size(settings)
        self.resize(w, h)
        self.apply_settings(settings, save=False)

    # ---------- 设置与外观 ----------

    def _logical_size(self, settings: Settings) -> tuple[int, int]:
        _, height = settings.logical_size()
        return max(1, round(height * self._source_w / self._source_h)), height

    def apply_settings(self, settings: Settings, save: bool = True) -> None:
        self._settings = settings
        w, h = self._logical_size(settings)
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, settings.always_on_top)
        self.setWindowFlag(Qt.WindowTransparentForInput, settings.click_through)
        if settings.click_through:
            self._cancel_drag()
        self._selector.reset()
        self._pose_cell = None
        self._controller.behavior.follow_target = None
        self._controller.configure_behavior(settings.natural_behavior)
        self._controller.behavior.configure_extras(settings.life_actions, settings.thinking_actions)
        if was_visible:
            self.show()  # 窗口标志变更需要重新显示（不抢焦点）
        self._poll_hover()
        self._update_timers()
        self.update()
        if save:
            self.settings_changed.emit(settings)

    def set_canvas_height(self, dip: int, save: bool = True) -> None:
        dip = max(MIN_CANVAS_HEIGHT_DIP, min(MAX_CANVAS_HEIGHT_DIP, int(dip)))
        if dip == self._settings.canvas_height_dip:
            return
        self._settings.canvas_height_dip = dip
        w, h = self._logical_size(self._settings)
        self.resize(w, h)
        self._update_timers()
        self.update()
        if save:
            self.settings_changed.emit(self._settings)

    # ---------- 帧推进与朝向轮询 ----------

    def _update_timers(self) -> None:
        s = self._settings
        visible = self.isVisible()
        self._controller.set_motion_active(visible and s.animations_enabled and not s.reduced_motion)
        animating = visible and s.animations_enabled and not s.reduced_motion and self._pose_cell is None
        if animating and not self._frame_timer.isActive():
            self._player.resume()
            self._schedule_next_frame()
        elif not animating:
            self._frame_timer.stop()
            self._player.pause()  # 冻结在当前帧，重绘不再按时间跳帧
        if visible and s.mouse_follow and s.animations_enabled and not s.reduced_motion:
            if not self._follow_timer.isActive():
                self._follow_timer.start()
        else:
            self._follow_timer.stop()
        if visible and s.hover_passthrough:
            self._hover_timer.start()
        else:
            self._hover_timer.stop()

    def _set_hover_active(self, active: bool) -> None:
        changed = self._hover_active != active
        self._hover_active = active
        self._set_surface_input_transparency(self, self._settings.click_through or active)
        if self._hover_companion is not None:
            self._hover_companion.set_hover_active(active)
        if changed:
            # Keep the existing ARGB surface; do not reset WS_EX_LAYERED.
            self.update()

    def set_hover_companion(self, companion) -> None:
        self._hover_companion = companion
        self._poll_hover()

    def _set_surface_input_transparency(self, surface: QWidget, through: bool) -> None:
        handle = surface.windowHandle()
        if handle is not None:
            if os.name == "nt" and QGuiApplication.platformName() == "windows":
                try:
                    if self._native_input_transparency is None:
                        from codex_pet.input_transparency import WindowsInputTransparency
                        self._native_input_transparency = WindowsInputTransparency()
                    self._native_input_transparency.set_enabled(int(surface.winId()), through)
                    self._input_error_logged = False
                except OSError:
                    if not self._input_error_logged:
                        log.warning("Native input transparency unavailable", exc_info=True)
                        self._input_error_logged = True
            elif bool(handle.flags() & Qt.WindowTransparentForInput) != through:
                handle.setFlag(Qt.WindowTransparentForInput, through)

    def _poll_hover(self) -> None:
        companion = self._hover_companion
        interactive = (
            self._drag_active
            or (companion is not None and companion.hover_interaction_active())
            or bool(QGuiApplication.queryKeyboardModifiers() & Qt.ControlModifier)
            or self._has_open_menu_or_dialog()
        )
        cursor = QCursor.pos()
        pos = self.mapFromGlobal(cursor)
        active = (
            self.isVisible() and self._settings.hover_passthrough and not interactive
            and ((self.rect().contains(pos) and self._alpha_hit(pos))
                 or (companion is not None and companion.hover_contains(cursor)))
        )
        self._set_hover_active(active)

    @staticmethod
    def _has_open_menu_or_dialog() -> bool:
        from PySide6.QtWidgets import QApplication

        return QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None

    def _schedule_next_frame(self) -> None:
        if self._frame_timer.isActive():
            return
        remaining = self._player.remaining_ms()
        if remaining is not None:
            self._frame_timer.start(int(max(remaining + 2.0, 16.0)))

    def _on_animation_changed(self) -> None:
        # An idle deadline must never delay the first/next frame of a new action.
        self._frame_timer.stop()
        self._pose_cell = None
        self._update_timers()
        self.update()

    def _on_frame_tick(self) -> None:
        self._controller.tick()
        self.update()
        if self.isVisible() and self._settings.animations_enabled and not self._settings.reduced_motion and self._pose_cell is None:
            self._schedule_next_frame()

    # ---------- 绘制 ----------

    def _display_cell(self) -> tuple[int, int]:
        if self._settings.reduced_motion:
            return self._catalog.static_reduced_cell
        if self._pose_cell is not None:
            return self._pose_cell
        return self._player.current_frame()

    def paintEvent(self, event: QEvent) -> None:
        dpr = self.devicePixelRatioF()
        if dpr != self._cached_dpr:
            self._cached_dpr = dpr
            self._renderer.clear_scaled()  # DPR 变化：旧目标尺寸缓存全部失效
        row, col = self._display_cell()
        phys = QSize(
            max(1, round(self.width() * dpr)),
            max(1, round(self.height() * dpr)),
        )
        # pixmap 的 DPR 用 物理/逻辑 的精确比值，使目标矩形与窗口逻辑尺寸严格一致；
        # 逻辑尺寸×屏幕 DPR 为非整数时由 Smooth 变换做一次平滑重采样（无锯齿）
        ratio = phys.width() / self.width()
        try:
            pm = self._renderer.pixmap(row, col, phys, ratio)
            self._last_good_pixmap = pm
            self._paint_error_logged = False
        except FileNotFoundError:
            # 帧缓存运行期被删/损坏（如杀软隔离）：沿用最近一帧，避免逐帧异常风暴
            pm = self._last_good_pixmap
            if not self._paint_error_logged:
                log.warning("帧缓存加载失败，%s", "沿用最近一帧" if pm is not None else "本帧留空")
                self._paint_error_logged = True
            if pm is None:
                painter = QPainter(self)
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.fillRect(self.rect(), Qt.transparent)
                painter.end()
                return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)  # 显式清屏，杜绝上一帧残留
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setOpacity(HOVER_OPACITY if self._hover_active else 1.0)
        painter.drawPixmap(0, 0, pm)
        painter.end()

    # ---------- 命中测试 ----------

    def _alpha_hit(self, pos: QPoint) -> bool:
        """窗口逻辑坐标 → 源帧坐标，按 alpha 判断是否命中人物。"""
        row, col = self._display_cell()
        if self.width() <= 0 or self.height() <= 0:
            return False
        fx = pos.x() * self._source_w / self.width()
        fy = pos.y() * self._source_h / self.height()
        try:
            return self._renderer.alpha_at(row, col, int(fx), int(fy)) >= ALPHA_HIT_THRESHOLD
        except FileNotFoundError:
            return False

    # ---------- 鼠标与键盘 ----------

    def _release_drag_capture(self) -> None:
        if QWidget.mouseGrabber() is self:
            self.releaseMouse()

    def _cancel_drag(self) -> None:
        was_dragging = self._drag_active
        self._drag_active = False
        self._drag_exceeded = False
        self._drag_press_global = None
        self._drag_last_global = None
        self._drag_hold_timer.stop()
        self._release_drag_capture()
        if was_dragging:
            self._controller.end_drag()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.UngrabMouse and getattr(self, '_drag_active', False):
            self._cancel_drag()
        return super().event(event)

    def nativeEvent(self, event_type, message):
        # A first press can arrive between Ctrl going down and the next hover
        # poll. Clear the stale input flag BEFORE Qt translates that native
        # message; otherwise Qt discards it before mousePressEvent can run.
        if event_type == b'windows_generic_MSG' and getattr(self, '_hover_active', False):
            import ctypes
            from ctypes import wintypes

            native = wintypes.MSG.from_address(int(message))
            if native.message in (0x0201, 0x0203) and QGuiApplication.queryKeyboardModifiers() & Qt.ControlModifier:
                self._set_hover_active(False)
        return super().nativeEvent(event_type, message)

    def enterEvent(self, event: QEvent) -> None:
        self._poll_hover()
        super().enterEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._poll_hover()
        if not self._settings.click_through and not self._hover_active and self._alpha_hit(event.pos()):
            self.context_menu_requested.emit(event.globalPos())
            event.accept()
        else:
            event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._poll_hover()
        if self._hover_active:
            event.ignore()
            return
        if event.button() == Qt.LeftButton and not self._settings.click_through:
            if not self._alpha_hit(event.position().toPoint()):
                event.ignore()
                return
            self._drag_active = True
            self._drag_exceeded = False
            self._drag_press_global = event.globalPosition().toPoint()
            self._drag_last_global = self._drag_press_global
            self._drag_dx_accum = 0.0
            self._drag_origin_pos = self.pos()
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self._controller.begin_drag()
            # Accept the FIRST press and explicitly retain move/release events,
            # including when the translucent Tool window was not active before it.
            self.grabMouse()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._poll_hover()
        if self._drag_active and self._drag_press_global is not None:
            gpos = event.globalPosition().toPoint()
            if not self._drag_exceeded:
                moved = math.hypot(
                    gpos.x() - self._drag_press_global.x(),
                    gpos.y() - self._drag_press_global.y(),
                )
                if moved < DRAG_THRESHOLD_DIP:
                    event.accept()
                    return
                self._drag_exceeded = True
            if self._drag_last_global is not None:
                self._drag_dx_accum += gpos.x() - self._drag_last_global.x()
            self._drag_last_global = gpos
            self._drag_hold_timer.start()
            if self._drag_dx_accum > DRAG_DIRECTION_DIP:
                self._controller.drag_moving("running-right")
                self._drag_dx_accum = 0.0
            elif self._drag_dx_accum < -DRAG_DIRECTION_DIP:
                self._controller.drag_moving("running-left")
                self._drag_dx_accum = 0.0
            self.move(gpos - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def _on_drag_hold(self) -> None:
        if self._drag_active:
            self._controller.drag_hold()
            self._drag_dx_accum = 0.0

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._drag_active:
            self._drag_active = False
            self._drag_hold_timer.stop()
            self._release_drag_capture()
            self._controller.end_drag()
            if not self._drag_exceeded:
                # 未超过拖动阈值的松开 = 单击人物 → 挥手（不影响任务状态）
                self._controller.on_click_character()
            self._drag_press_global = None
            self._drag_last_global = None
            self._save_timer.start()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._poll_hover()
        if event.modifiers() & Qt.ControlModifier and not self._settings.click_through:
            if self._alpha_hit(event.position().toPoint()):
                steps = event.angleDelta().y() / 120.0
                self.set_canvas_height(
                    int(self._settings.canvas_height_dip + steps * WHEEL_STEP_DIP)
                )
                event.accept()
                return
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape and self._drag_active:
            # 取消当前拖动：回到拖动前位置
            self._cancel_drag()
            self.move(self._drag_origin_pos)
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------- 朝向跟随 ----------

    def _poll_follow(self) -> None:
        if self._drag_active or self._player.current_name != "idle":
            return
        cursor = QCursor.pos()
        center = self.frameGeometry().center()
        dx = cursor.x() - center.x()
        dy = cursor.y() - center.y()
        pose = self._selector.select(dx, dy, math.hypot(dx, dy))
        if self._settings.natural_behavior:
            self._controller.behavior.follow_target = pose
            return  # The director owns the bounded glance; never pause the player here.
        cell = self._catalog.pose_cell(pose) if pose is not None else None
        if cell != self._pose_cell:
            self._pose_cell = cell
            self._update_timers()
            self.update()

    # ---------- 显示/隐藏与位置 ----------

    def showEvent(self, event: QEvent) -> None:
        self.ensure_visible_on_screen()
        self._player.resume()
        handle = self.windowHandle()
        if handle is not None and handle is not self._screen_signal_handle:
            try:
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_signal_handle = handle
            except RuntimeError:
                pass
        self._update_timers()
        self._poll_hover()
        super().showEvent(event)

    def hideEvent(self, event: QEvent) -> None:
        self._cancel_drag()
        self._frame_timer.stop()
        self._follow_timer.stop()
        self._hover_timer.stop()
        self._set_hover_active(False)
        self._player.pause()
        self._controller.set_motion_active(False)
        self._save_position()
        super().hideEvent(event)

    def _on_screen_changed(self, screen) -> None:
        self._renderer.clear_scaled()
        self.ensure_visible_on_screen()
        self.update()

    def visible_area_intersects_screens(self) -> bool:
        rect = self.frameGeometry()
        return any(
            rect.intersects(screen.availableGeometry()) for screen in QGuiApplication.screens()
        )

    def ensure_visible_on_screen(self) -> None:
        """显示器拔掉/分辨率变化后把窗口拉回可见工作区（验收 B08）。"""
        if self.visible_area_intersects_screens():
            return
        self.reset_position()

    def reset_position(self) -> None:
        """Explicit reset also works when the old position is technically on-screen."""
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        avail = primary.availableGeometry()
        w, h = self._settings.logical_size()
        x = avail.right() - w - 24
        y = avail.bottom() - h - 24
        self.move(max(avail.left(), x), max(avail.top(), y))

    def _save_position(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        pos = self.pos()
        self._settings.screen_name = screen.name() if screen else None
        self._settings.pos_x = float(pos.x())
        self._settings.pos_y = float(pos.y())
        self.settings_changed.emit(self._settings)

    def restore_position(self) -> None:
        """按设置恢复位置；屏幕缺失或越界时钳制回可见区。"""
        if self._settings.pos_x is None or self._settings.pos_y is None:
            self.reset_position()
            return
        self.move(int(self._settings.pos_x), int(self._settings.pos_y))
        self.ensure_visible_on_screen()

    # ---------- 调试 ----------

    def debug_info(self) -> dict:
        dpr = self.devicePixelRatioF()
        row, col = self._display_cell()
        return {
            "frame_cell": [row, col],
            "source_frame_px": [self._source_w, self._source_h],
            "logical_size": [self.width(), self.height()],
            "physical_px": [round(self.width() * dpr), round(self.height() * dpr)],
            "dpr": round(dpr, 3),
            "animation": self._player.current_name,
            "natural_behavior": self._settings.natural_behavior,
            "behavior_mode": self._controller.behavior.mode,
            "behavior_phase": self._controller.behavior.phase,
            "pose_override": self._pose_cell is not None,
            "hover_passthrough": self._settings.hover_passthrough,
            "hover_active": self._hover_active,
            "sprite_opacity": HOVER_OPACITY if self._hover_active else 1.0,
            "task_status": self._controller.task_status.value,
            "screen": self.screen().name() if self.screen() else None,
            "renderer": self._renderer.memory_report(),
        }
