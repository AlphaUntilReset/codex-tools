"""Minimal underfoot status capsule and a separate non-activating task list."""
from __future__ import annotations

import time
from dataclasses import replace

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from codex_pet.bridge import BridgeState, Connection, TaskStatus
from codex_pet.window import HOVER_OPACITY
from codex_pet.bridge.plan_usage import (
    GRADE_COLORS,
    FRESH_USAGE_S,
    HOLD_LAST_GOOD_S,
    NOISE_STATES,
    PlanUsage,
    UsageState,
    usage_grade,
    usage_freshness,
    usage_pill_parts,
    usage_short_text,
    usage_text,
)

LABELS = {
    TaskStatus.RUNNING: "工作中", TaskStatus.READY: "完成未读",
    TaskStatus.COMPLETED: "已完成", TaskStatus.BLOCKED: "任务受阻",
    TaskStatus.NEEDS_INPUT: "等待输入", TaskStatus.IDLE: "已停止",
    TaskStatus.UNKNOWN: "状态待确认",
}
ORDER = {s: i for i, s in enumerate((TaskStatus.NEEDS_INPUT, TaskStatus.BLOCKED,
    TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.IDLE, TaskStatus.UNKNOWN))}
ATTENTION = {TaskStatus.NEEDS_INPUT, TaskStatus.BLOCKED, TaskStatus.READY}
COLORS = {
    TaskStatus.RUNNING: "#39836e", TaskStatus.READY: "#b48136",
    TaskStatus.BLOCKED: "#b65f62", TaskStatus.NEEDS_INPUT: "#b48136",
    TaskStatus.COMPLETED: "#84948a", TaskStatus.IDLE: "#84948a",
    TaskStatus.UNKNOWN: "#929991",
}


def ordered_tasks(state: BridgeState):
    if state.connection is not Connection.CONNECTED:
        return []
    return sorted(state.tasks.values(), key=lambda t: (-1 if t.attention_reason else ORDER[t.status], -t.seq, t.task_id))


class StatusPill(QPushButton):
    """The whole capsule is clickable; the chevron is only a visual hint."""
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(True)  # Checked = compact.
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(82, 26)
        self.summary = "未连接"
        self.usage = ""
        self.usage_color = ""
        self._demo = False
        self.color = "#929991"
        self.set_summary(self.summary, self.color, False)

    def set_summary(self, summary, color, demo):
        self.summary, self.color, self._demo = summary, color, demo
        self._relayout()

    def set_usage(self, usage: str, color: str = ""):
        """常驻余额段（如 "剩余 73% · 3天"）与标色；空串隐藏。独立于任务摘要更新。"""
        self.usage = usage or ""
        self.usage_color = color or ""
        self._relayout()

    def _relayout(self):
        text = ("演示 · " if self._demo else "") + self.summary
        if self.usage:
            text += f" · {self.usage}"
        font = QFont(self.font())
        font.setPixelSize(11)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 40
        # 偶数 DIP：150% 缩放下宽高恰好落在整数物理像素，
        # 奇数宽度（如 171→256.5 物理）会让整个位图半像素重采样，边缘发糊出颗粒。
        width = min(220, max(74, width))
        self.setFixedWidth(int(width // 2 * 2))
        self.setText(text)
        self.setAccessibleName(text)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#d3dcd3" if self.underMouse() else "#dfe4dc"), 1))
        p.setBrush(QColor("#f2f6ee" if self.underMouse() else "#fffdf8"))
        # 半径取偶数 DIP（12→18 整物理像素），弧线逐行边界均匀。
        p.drawRoundedRect(QRectF(.5, .5, self.width()-1, self.height()-1), 12, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        # 偶数 DIP 坐标/尺寸：150% 缩放下精确落在整数物理像素，避免圆点半像素发虚。
        p.drawEllipse(QRectF(8, 10, 6, 6))
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        avail = self.width() - 39
        metrics = QFontMetrics(font)
        if self.usage:
            # 分段绘制：任务摘要默认色，余额段按健康度标色（绿/红），超宽优先保余额。
            prefix = ("演示 · " if self._demo else "") + self.summary + " · "
            usage_w = metrics.horizontalAdvance(self.usage)
            if metrics.horizontalAdvance(prefix) + usage_w > avail:
                prefix = metrics.elidedText(prefix, Qt.ElideRight, max(20, avail - usage_w))
            p.setPen(QColor("#56635a"))
            p.drawText(QRectF(20, 0, avail, 26), Qt.AlignVCenter, prefix)
            p.setPen(QColor(self.usage_color) if self.usage_color else QColor("#56635a"))
            x2 = 20 + metrics.horizontalAdvance(prefix)
            p.drawText(QRectF(x2, 0, avail - x2 + 20, 26), Qt.AlignVCenter, self.usage)
        else:
            p.setPen(QColor("#56635a"))
            text = metrics.elidedText(self.text(), Qt.ElideRight, avail)
            p.drawText(QRectF(20, 0, avail, 26), Qt.AlignVCenter, text)
        p.setPen(QPen(QColor("#869286"), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = self.width()-12, 13
        dy = -2 if self.isChecked() else 2
        p.drawLine(QPointF(cx-3, cy-dy), QPointF(cx, cy+dy))
        p.drawLine(QPointF(cx, cy+dy), QPointF(cx+3, cy-dy))


class TaskRow(QPushButton):
    """Title first, status beneath; one compact clickable row per task."""
    def __init__(self, task, demo):
        super().__init__()
        self.title = task.title or f"任务 {task.task_id[:8]}"
        self.label = task.attention_reason or LABELS[task.status]
        self.color = "#b48136" if task.attention_reason else COLORS[task.status]
        self.setText(f"{self.label}\n{self.title}")
        self.setAccessibleName(self.text())
        self.setToolTip(f"{self.title}\n{self.label}" + ("" if demo else "\n点击返回此任务"))
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setEnabled(not demo)
        self.setFixedHeight(44)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() and self.isEnabled():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#eef3eb"))
            p.drawRoundedRect(self.rect(), 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        # 偶数 DIP 圆点：150% 缩放下落在整数物理像素，避免半像素发虚。
        p.drawEllipse(QRectF(6, 12, 6, 6))
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        p.setPen(QColor("#34453b"))
        title = QFontMetrics(font).elidedText(self.title, Qt.ElideRight, self.width()-44)
        p.drawText(QRectF(20, 3, self.width()-40, 21), Qt.AlignVCenter, title)
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(QColor(self.color))
        label = QFontMetrics(font).elidedText(self.label, Qt.ElideRight, self.width()-40)
        p.drawText(QRectF(20, 23, self.width()-36, 17), Qt.AlignVCenter, label)
        p.setPen(QPen(QColor("#c2ccc0"), 1.2, Qt.SolidLine, Qt.RoundCap))
        x = self.width()-13
        p.drawLine(QPointF(x-2, 18), QPointF(x+1, 21))
        p.drawLine(QPointF(x+1, 21), QPointF(x-2, 24))


class TaskDetails(QFrame):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#dfe4dc"), 1))
        p.setBrush(QColor("#fffdf8"))
        p.drawRoundedRect(QRectF(.5, .5, self.width()-1, self.height()-1), 10, 10)


class TaskPanel(QFrame):
    open_task = Signal(str)
    preferences_changed = Signal(bool, bool)

    def __init__(self, pet: QWidget, *, clock=time.time):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        super().__init__(pet, flags)
        self.setObjectName("taskPanel")
        self.setWindowTitle("桌宠 · Codex 任务")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self._pet = pet
        self._clock = clock
        self._enabled = True
        self._state = BridgeState("none", "disabled", Connection.DISABLED)
        self._signature = None
        self._placing = False
        self._hover_active = False
        self._hover_interacting = False
        self._click_through = False
        self._hover_poll = getattr(pet, '_poll_hover', None)
        self._input_policy = getattr(pet, '_set_surface_input_transparency', None)
        self.setStyleSheet("""
            QLabel {color:#7b887c; font-size:11px; background:transparent;}
            QLabel#usageLine {color:#8d9a8e; font-size:10px;}
            QScrollArea {border:0; background:transparent;}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.collapse = StatusPill()
        # Fade the complete child painting once, without changing the native
        # window opacity or its layered backing surface (0.4.7.8 contract).
        self._hover_effect = QGraphicsOpacityEffect(self.collapse)
        self._hover_effect.setOpacity(HOVER_OPACITY)
        self._hover_effect.setEnabled(False)
        self.collapse.setGraphicsEffect(self._hover_effect)
        self.setMouseTracking(True)
        self.collapse.setMouseTracking(True)
        self.installEventFilter(self)
        self.collapse.installEventFilter(self)
        self.collapse.clicked.connect(self.set_collapsed)
        outer.addWidget(self.collapse)
        # Separate tool window: details never enlarge the capsule's desktop footprint.
        self.details = TaskDetails(self, flags)
        self.details.setObjectName("taskDetails")
        self.details.setWindowTitle("桌宠 · 任务详情")
        self.details.setAttribute(Qt.WA_ShowWithoutActivating)
        self.details.setAttribute(Qt.WA_TranslucentBackground)
        self.details.setFixedWidth(230)
        inner = QVBoxLayout(self.details)
        inner.setContentsMargins(6, 6, 6, 6)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background:transparent;")
        self.rows = QWidget()
        self.rows.setStyleSheet("background:transparent;")
        self.rows_layout = QVBoxLayout(self.rows)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(1)
        self.rows_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.rows)
        inner.addWidget(self.scroll)
        # 套餐用量页脚：余额行 + 重置时刻行；真实数据缺失时只说明原因，不显示假数字。
        self._usage: PlanUsage | None = None
        self._last_good_usage: PlanUsage | None = None
        self._fetch_note = ""
        self._usage_rendered_at = self._clock()
        self._usage_timer = QTimer(self)
        self._usage_timer.setSingleShot(True)
        self._usage_timer.setTimerType(Qt.PreciseTimer)
        self._usage_timer.timeout.connect(self._refresh_usage)
        self.usage_label = QLabel("")
        self.usage_label.setObjectName("usageLine")
        self.usage_label.setAlignment(Qt.AlignCenter)
        self.usage_label.setVisible(False)
        inner.addWidget(self.usage_label)
        self.details.hide()
        self.setFixedSize(self.collapse.width(), 26)
        pet.installEventFilter(self)
        attach = getattr(pet, 'set_hover_companion', None)
        if callable(attach):
            attach(self)

    def apply_settings(self, settings) -> None:
        self._click_through = settings.click_through
        self._enabled = settings.show_task_panel
        self.collapse.setChecked(settings.task_panel_collapsed)
        for window in (self, self.details):
            window.setWindowFlag(Qt.WindowStaysOnTopHint, settings.always_on_top)
            window.setWindowFlag(Qt.WindowTransparentForInput, settings.click_through)
        self._resize_and_place()
        self._sync_visibility()
        if callable(self._hover_poll):
            self._hover_poll()

    def hover_contains(self, global_pos) -> bool:
        if not self.isVisible():
            return False
        path = QPainterPath()
        path.addRoundedRect(QRectF(.5, .5, self.width()-1, self.height()-1), 12, 12)
        return path.contains(QPointF(self.mapFromGlobal(global_pos)))

    def hover_interaction_active(self) -> bool:
        return self._hover_interacting

    def set_hover_active(self, active: bool) -> None:
        active = bool(active and self.isVisible())
        if self._hover_active != active:
            self._hover_active = active
            self._hover_effect.setEnabled(active)
            self.collapse.update()
        if callable(self._input_policy):
            self._input_policy(self, self._click_through or active)

    def set_state(self, state: BridgeState) -> None:
        self._state = state
        tasks = ordered_tasks(state)
        signature = (state.connection, state.mode, state.is_demo, state.capabilities,
                     tuple((t.task_id, t.title, t.status, t.turn_id, t.unread, t.attention_reason) for t in tasks))
        if signature != self._signature:
            self._signature = signature
            while self.rows_layout.count():
                item = self.rows_layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                    item.widget().deleteLater()
            color = COLORS[TaskStatus.UNKNOWN]
            if state.connection is Connection.CONNECTED:
                attention = sum(t.status in ATTENTION or bool(t.attention_reason) for t in tasks)
                running = sum(t.status is TaskStatus.RUNNING for t in tasks)
                parts = []
                if attention:
                    parts.append(f"待处理 {attention}")
                if running:
                    parts.append(f"工作中 {running}")
                if not parts:
                    completed = sum(t.status is TaskStatus.COMPLETED for t in tasks)
                    parts.append(f"已完成 {completed}" if completed else "暂无活动")
                summary = " · ".join(parts)
                color = "#b48136" if any(t.attention_reason for t in tasks) else COLORS[state.aggregate_status()]
                for task in tasks:
                    button = TaskRow(task, state.is_demo)
                    button.clicked.connect(lambda checked=False, tid=task.task_id: self.open_task.emit(tid))
                    self.rows_layout.addWidget(button)
                if not tasks:
                    self._empty_row("暂无任务")
            else:
                summary = {Connection.CONNECTING: "连接中", Connection.DISCONNECTED: "已断开",
                           Connection.UNSUPPORTED: "暂不可用"}.get(state.connection, "未连接")
                self._empty_row("暂时读不到任务状态")
            self.collapse.set_summary(summary, color, state.is_demo)
            self._update_tooltip()
            self.scroll.setFixedHeight(min(179, len(tasks)*45-1) if tasks else 32)
            self._resize_and_place()
        self._sync_visibility()

    def set_plan_usage(self, usage: PlanUsage | None, now: float | None = None) -> None:
        """网络噪声保留快照；时效由显示层重新评估，认证失效立即清缓存。"""
        at = now if now is not None else self._clock()
        if usage is not None and usage.state in NOISE_STATES and self._last_good_usage is not None:
            self._usage = self._last_good_usage
            self._fetch_note = "暂时取不到新数据，以上为上次成功值"
        else:
            self._fetch_note = ""
            if usage is not None and usage.state is UsageState.OK and usage.fetched_at is None:
                usage = replace(usage, fetched_at=at)
            self._usage = usage
            self._last_good_usage = usage if usage is not None and usage.state is UsageState.OK else None
        self._refresh_usage(at)

    def _refresh_usage(self, now: float | None = None) -> None:
        """即使网络没有返回，也在重置/变旧截止时间重新绘制。"""
        at = now if now is not None else self._clock()
        self._usage_rendered_at = at
        self._usage_timer.stop()
        usage = self._usage
        self.usage_label.setText(usage_short_text(usage, at))
        self.usage_label.setVisible(bool(self.usage_label.text()))
        grade = GRADE_COLORS[usage_grade(usage, at)]
        self.usage_label.setStyleSheet(
            f"color:{grade}; background:transparent;" if grade else "background:transparent;"
        )
        text, color = usage_pill_parts(usage, at)
        self.collapse.set_usage(text, color)
        self._update_tooltip()
        self._resize_and_place()
        if usage is not None and usage.state is UsageState.OK and usage_freshness(usage, at) in ("fresh", "stale"):
            deadlines = [at + 60, usage.reset_at]
            if usage.fetched_at is not None:
                deadlines += [usage.fetched_at + FRESH_USAGE_S, usage.fetched_at + HOLD_LAST_GOOD_S]
            delay = min(t - at for t in deadlines if t is not None and t > at)
            self._usage_timer.start(max(1, int(delay * 1000) + 1))

    def _update_tooltip(self) -> None:
        tip = f"{self.collapse.text()}\n点击展开 / 收起"
        line = usage_text(self._usage, self._usage_rendered_at)
        if line:
            note = f"（{self._fetch_note}）" if self._fetch_note else ""
            tip += f"\n{line}{note}"
            tip += "\n余额标色：绿=比均匀预算富余 · 红=用量超前或额度受限；±15 个百分点以外标色"
        self.setToolTip(tip)
        self.details.setToolTip(tip)

    def _empty_row(self, text):
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        self.rows_layout.addWidget(label)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapse.setChecked(collapsed)
        self.collapse.setAccessibleDescription("点击展开任务" if collapsed else "点击收起任务")
        self._resize_and_place()
        self._sync_visibility()
        self.preferences_changed.emit(collapsed, False)

    def show_open_error(self):
        self.setToolTip("无法打开此任务，请从 Codex 中查找")
        self.details.setToolTip(self.toolTip())

    @staticmethod
    def _rounded_mask(widget, radius):
        path = QPainterPath()
        path.addRoundedRect(QRectF(widget.rect()), radius, radius)
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _resize_and_place(self) -> None:
        self.setFixedSize(self.collapse.width(), 26)
        usage_h = self.usage_label.sizeHint().height() + 1 if self.usage_label.text() else 0
        # 高度同样偶数 DIP 化，避免 150% 缩放下半像素重采样。
        details_h = self.scroll.height() + usage_h + 12
        self.details.setFixedHeight(int(details_h // 2 * 2))
        self.layout().activate()
        self.details.layout().activate()
        # 不再 setMask：QRegion 逐像素硬裁剪把抗锯齿圆角砍成阶梯颗粒；
        # 半透明窗口 + 抗锯齿绘制本身就是平滑边缘（QPainterPath/QRegion 仅保留备用）。
        self.reposition()

    def reposition(self) -> None:
        screen = self._pet.screen()
        if screen is None or self._placing:
            return
        self._placing = True
        try:
            area = screen.availableGeometry().adjusted(3, 3, -3, -3)
            pet = self._pet.frameGeometry()
            # Idle source: feet occupy x=306..518, bottom y=812 in the 768x832 cell.
            # Keep this anchor stable through blinks/runs/jumps instead of bobbing each frame.
            foot_y = pet.top() + round(pet.height()*812/832)
            bar_y = foot_y + 2
            overflow = bar_y + self.height() - area.bottom() - 1
            # 拖拽进行中不动宠物本体：mouseMove 每次把窗口推回光标处，会与这里的
            # 上移打架，窗口在两个位置间高频闪跳（越界拖拽残影）。松手后 800ms 防抖
            # 保存触发 apply_settings→reposition，仍会按原设计上移一次落位。
            dragging = getattr(self._pet, "_drag_active", False)
            if (overflow > 0 and self._enabled and self._state.mode != "disabled"
                    and self._pet.isVisible() and not dragging):
                new_y = max(area.top(), pet.top()-overflow)
                self._pet.move(pet.left(), new_y)
                pet = self._pet.frameGeometry()
                bar_y = pet.top()+round(pet.height()*812/832)+2
                save_timer = getattr(self._pet, "_save_timer", None)
                if save_timer is not None:
                    save_timer.start()
            center_x = pet.left()+round(pet.width()*412/768)
            x = max(area.left(), min(center_x-self.width()//2, area.right()-self.width()+1))
            y = max(area.top(), min(bar_y, area.bottom()-self.height()+1))
            self.move(x, y)
            # Details sit beside the character, keeping the permanent capsule underfoot.
            left = pet.left()+round(pet.width()*172/768)-8-self.details.width()
            right = pet.left()+round(pet.width()*596/768)+8
            detail_x = left if left >= area.left() else right
            detail_x = max(area.left(), min(detail_x, area.right()-self.details.width()+1))
            detail_y = max(area.top(), min(y-self.details.height()-5, area.bottom()-self.details.height()+1))
            self.details.move(detail_x, detail_y)
        finally:
            self._placing = False

    def _sync_visibility(self) -> None:
        show = self._enabled and self._pet.isVisible() and self._state.mode != "disabled"
        if show:
            self.reposition()
            if not self.isVisible():
                self.show()
            self.details.setVisible(not self.collapse.isChecked())
        else:
            self.details.hide()
            self.hide()

    def hideEvent(self, event):
        self._hover_interacting = False
        self.set_hover_active(False)
        self.details.hide()
        if callable(self._hover_poll):
            QTimer.singleShot(0, self._hover_poll)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if watched in (self, self.collapse) and callable(self._hover_poll):
            kind = event.type()
            if kind in (QEvent.Enter, QEvent.MouseMove, QEvent.MouseButtonPress):
                self._hover_poll()
            if watched is self.collapse:
                if kind == QEvent.MouseButtonPress:
                    if self._hover_active:
                        return True
                    if event.button() == Qt.LeftButton:
                        self._hover_interacting = True
                elif kind in (QEvent.MouseButtonRelease, QEvent.UngrabMouse):
                    self._hover_interacting = False
                    QTimer.singleShot(0, self._hover_poll)
        if watched is self._pet:
            if event.type() in (QEvent.Move, QEvent.Resize):
                self.reposition()
            elif event.type() == QEvent.Hide:
                self.details.hide()
                self.hide()
            elif event.type() == QEvent.Show:
                QTimer.singleShot(0, self._sync_visibility)
        return super().eventFilter(watched, event)
