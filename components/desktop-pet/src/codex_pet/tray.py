"""系统托盘：菜单（显示/隐藏、大小、置顶、暂停动作、跟随、穿透、重置位置、退出）。"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QMenu, QSlider, QSystemTrayIcon, QVBoxLayout

from codex_pet import __version__
from codex_pet.controller import MANUAL_ACTIONS
from codex_pet.settings import MAX_CANVAS_HEIGHT_DIP, MIN_CANVAS_HEIGHT_DIP, Settings

log = logging.getLogger("codex_pet.tray")

ACTION_LABELS = {
    "waving": "挥手（手动）",
    "jumping": "跳跃（手动）",
    "failed": "出错提示（手动）",
    "review": "审阅（手动）",
    "yawn": "打哈欠（手动）",
    "groom": "整理头发（手动）",
    "thinking": "托腮思考（手动）",
}


CONNECTION_TEXT = {
    "disabled": "未启用",
    "connecting": "连接中",
    "connected": "已连接",
    "disconnected": "已断开",
    "unsupported": "本机不支持",
}

STATUS_TEXT = {
    "running": "任务运行中",
    "needs_input": "等待输入",
    "ready": "有任务完成",
    "completed": "任务已完成",
    "blocked": "任务受阻",
    "idle": "无活动任务",
    "unknown": "无实时状态",
}


def bridge_status_text(state) -> str:
    """托盘提示用的桥接状态摘要（独立模式返回 None 语义由调用方处理）。"""
    if state.mode == "disabled":
        return None
    label = "演示" if state.is_demo else ("Codex 只读同步" if state.mode == "local-ro" else "Codex")
    conn = CONNECTION_TEXT.get(state.connection.value, state.connection.value)
    parts = [f"桌宠 · {label}：{conn}"]
    if state.connection.value == "connected":
        if state.tasks:
            counts: dict[str, int] = {}
            for task in state.tasks.values():
                counts[task.status.value] = counts.get(task.status.value, 0) + 1
            summary = "，".join(
                f"{STATUS_TEXT.get(v, v)}×{n}" for v, n in counts.items()
            )
            parts.append(summary)
        else:
            parts.append("无活动任务（等你的 Codex 开工）")
    return "\n".join(parts)


class SettingsDialog(QDialog):
    """轻量设置窗口：大小滑块 + 开关。"""

    def __init__(self, settings: Settings, parent=None, *, available_actions=None):
        super().__init__(parent)
        self.setWindowTitle("桌宠 设置")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)

        size_label = QLabel(f"大小（画布高度 {MIN_CANVAS_HEIGHT_DIP}–{MAX_CANVAS_HEIGHT_DIP}）")
        layout.addWidget(size_label)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(MIN_CANVAS_HEIGHT_DIP, MAX_CANVAS_HEIGHT_DIP)
        self.slider.setValue(settings.canvas_height_dip)
        self.slider.valueChanged.connect(
            lambda v: size_label.setText(f"大小（画布高度 {v}）")
        )
        layout.addWidget(self.slider)
        self._checkboxes: dict[str, object] = {}

        def add_check(key: str, label: str):
            from PySide6.QtWidgets import QCheckBox

            cb = QCheckBox(label)
            cb.setChecked(getattr(settings, key))
            layout.addWidget(cb)
            self._checkboxes[key] = cb

        add_check("always_on_top", "总在最前（不抢焦点）")
        add_check("animations_enabled", "播放动画")
        add_check("natural_behavior", "自然动作（眨眼、张望与工作停顿）")
        if available_actions is None or set(available_actions)&{'yawn','groom'}:
            add_check("life_actions", "生活小动作")
        if available_actions is None or 'thinking' in available_actions:
            add_check("thinking_actions", "工作时思考动作")
        add_check("reduced_motion", "减少动态（静态显示）")
        add_check("mouse_follow", "鼠标跟随（看向鼠标方向）")
        add_check("hover_passthrough", "鼠标移到人物或胶囊时变淡并穿透（按住 Ctrl 可交互）")
        add_check("click_through", "整体鼠标穿透（托盘可恢复）")
        add_check("show_on_start", "启动时显示")
        add_check("show_task_panel", "显示人物旁的 Codex 任务卡片")
        add_check("global_hotkey", "Ctrl+Alt+P 显示 / 隐藏宠物")
        add_check("autostart", "开机自启（随官方 Codex 一起拉起）")
        add_check("follow_codex_visibility", "随官方 Codex 显隐（官方退出后隐藏，重启后现身）")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def result_settings(self, base: Settings) -> Settings:
        s = Settings(**{k: getattr(base, k) for k in base.__dict__})
        s.canvas_height_dip = self.slider.value()
        for key, cb in self._checkboxes.items():
            setattr(s, key, cb.isChecked())
        return s


class DebugDialog(QDialog):
    def __init__(self, info_provider: Callable[[], dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("桌宠 调试信息")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)
        self._label = QLabel()
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._label)
        self._provider = info_provider
        self.refresh()

    def refresh(self) -> None:
        info = self._provider()
        lines = ["<table cellspacing=4>"]
        for key, value in info.items():
            lines.append(f"<tr><td><b>{key}</b></td><td>{value}</td></tr>")
        lines.append(f"<tr><td><b>version</b></td><td>{__version__}</td></tr>")
        lines.append("</table>")
        self._label.setText("".join(lines))


class TrayController(QSystemTrayIcon):
    toggle_visibility = Signal()
    reset_position = Signal()
    apply_settings = Signal(object)
    manual_action = Signal(str)
    set_bridge_mode = Signal(str)
    quit_app = Signal()
    open_debug = Signal()
    open_settings = Signal()

    def __init__(self, icon: QIcon, settings: Settings, parent=None, *, available_actions=None):
        super().__init__(icon, parent)
        self._settings = settings
        self._extra_checks = {}
        self.setToolTip("桌宠桌面宠物")
        menu = QMenu(parent)

        show_action = menu.addAction("显示 / 隐藏")
        show_action.triggered.connect(self.toggle_visibility.emit)
        self._panel_action = menu.addAction("显示任务卡片")
        self._panel_action.setCheckable(True)
        self._panel_action.setChecked(settings.show_task_panel)
        self._panel_action.toggled.connect(lambda on: self._patch(show_task_panel=on))

        size_menu = menu.addMenu("大小")
        for height, label in [(200, "小 (200)"), (320, "默认 (320)"), (480, "大 (480)"), (640, "最大 (640)")]:
            act = size_menu.addAction(label)
            act.triggered.connect(lambda _=False, h=height: self._emit_height(h))
        size_menu.addSeparator()
        custom = size_menu.addAction("自定义…")
        custom.triggered.connect(self.open_settings.emit)

        self._top_action = menu.addAction("总在最前")
        self._top_action.setCheckable(True)
        self._top_action.setChecked(settings.always_on_top)
        self._top_action.toggled.connect(lambda on: self._patch(always_on_top=on))

        self._anim_action = menu.addAction("播放动画")
        self._anim_action.setCheckable(True)
        self._anim_action.setChecked(settings.animations_enabled)
        self._anim_action.toggled.connect(lambda on: self._patch(animations_enabled=on))

        self._natural_action = menu.addAction("自然动作")
        self._natural_action.setCheckable(True)
        self._natural_action.setChecked(settings.natural_behavior)
        self._natural_action.toggled.connect(lambda on: self._patch(natural_behavior=on))

        self._follow_action = menu.addAction("鼠标跟随")
        self._follow_action.setCheckable(True)
        self._follow_action.setChecked(settings.mouse_follow)
        self._follow_action.toggled.connect(lambda on: self._patch(mouse_follow=on))

        self._through_action = menu.addAction("整体鼠标穿透")
        self._through_action.setCheckable(True)
        self._through_action.setChecked(settings.click_through)
        self._through_action.toggled.connect(lambda on: self._patch(click_through=on))

        self._hover_action = menu.addAction("鼠标移到人物或胶囊时变淡并穿透（Ctrl 可交互）")
        self._hover_action.setCheckable(True)
        self._hover_action.setChecked(settings.hover_passthrough)
        self._hover_action.toggled.connect(lambda on: self._patch(hover_passthrough=on))

        self._autostart_action = menu.addAction("开机自启（随官方 Codex 一起）")
        self._autostart_action.setCheckable(True)
        self._autostart_action.setChecked(settings.autostart)
        self._autostart_action.toggled.connect(lambda on: self._patch(autostart=on))

        self._codex_follow_action = menu.addAction("随官方 Codex 显隐（它关我也藏）")
        self._codex_follow_action.setCheckable(True)
        self._codex_follow_action.setChecked(settings.follow_codex_visibility)
        self._codex_follow_action.toggled.connect(lambda on: self._patch(follow_codex_visibility=on))

        for key, label in (("life_actions", "生活小动作"), ("thinking_actions", "工作时思考动作")):
            if available_actions is not None and not (set(available_actions) &
                    ({'yawn','groom'} if key=='life_actions' else {'thinking'})):
                continue
            check = menu.addAction(label)
            check.setCheckable(True)
            check.setChecked(getattr(settings, key))
            check.toggled.connect(lambda on, k=key: self._patch(**{k: on}))
            self._extra_checks[key] = check
        action_menu = menu.addMenu("动作（手动演示）")
        for name in MANUAL_ACTIONS:
            if name not in (available_actions if available_actions is not None else MANUAL_ACTIONS[:4]):
                continue
            act = action_menu.addAction(ACTION_LABELS.get(name, name))
            act.triggered.connect(lambda _=False, n=name: self.manual_action.emit(n))

        bridge_menu = menu.addMenu("Codex 联动")
        self._bridge_actions: dict[str, QAction] = {}
        bridge_labels = [
            ("disabled", "独立模式（默认，不连接）"),
            ("codex-local", "只读同步本机任务（尽力·非官方接口）"),
            ("demo", "演示事件（明确标注，非真实联动）"),
        ]
        for mode, label in bridge_labels:
            act = bridge_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(settings.bridge_mode == mode)
            act.triggered.connect(lambda _=False, m=mode: self.set_bridge_mode.emit(m))
            self._bridge_actions[mode] = act

        menu.addSeparator()
        reset_action = menu.addAction("重置位置")
        reset_action.triggered.connect(self.reset_position.emit)
        settings_action = menu.addAction("设置…")
        settings_action.triggered.connect(self.open_settings.emit)
        debug_action = menu.addAction("调试信息")
        debug_action.triggered.connect(self.open_debug.emit)
        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.quit_app.emit)

        self.setContextMenu(menu)

        # 宠物身上的右键菜单：贴近官方（官方右键只有 Hide），只留高频项；
        # 完整设置面在托盘菜单。
        pet_menu = QMenu(parent)
        hide_action = pet_menu.addAction("隐藏")
        hide_action.triggered.connect(self.toggle_visibility.emit)
        pet_size = pet_menu.addMenu("大小")
        for height, label in [(200, "小 (200)"), (320, "默认 (320)"), (480, "大 (480)"), (640, "最大 (640)")]:
            act = pet_size.addAction(label)
            act.triggered.connect(lambda _=False, h=height: self._emit_height(h))
        pet_custom = pet_size.addAction("自定义…")
        pet_custom.triggered.connect(self.open_settings.emit)
        pet_settings = pet_menu.addAction("设置…")
        pet_settings.triggered.connect(self.open_settings.emit)
        pet_menu.addSeparator()
        pet_quit = pet_menu.addAction("退出")
        pet_quit.triggered.connect(self.quit_app.emit)
        self._pet_menu = pet_menu

        self.activated.connect(self._on_activated)

    def pet_menu(self) -> QMenu:
        """宠物窗口右键使用的精简菜单。"""
        return self._pet_menu

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.toggle_visibility.emit()

    def _emit_height(self, height: int) -> None:
        self._patch(canvas_height_dip=height)

    def _patch(self, **changes) -> None:
        # 必须发副本：app.apply_settings 靠"新对象字段 != 旧对象字段"分发变更，
        # 原地改共享对象会让差值守卫变成对象与自身比较（0.4.3 P0，0.4.4 修复）。
        s = Settings(**{k: getattr(self._settings, k) for k in self._settings.__dict__})
        for key, value in changes.items():
            setattr(s, key, value)
        self.apply_settings.emit(s)

    def sync_from_settings(self, settings: Settings) -> None:
        self._settings = settings
        blockers = [QSignalBlocker(a) for a in (self._top_action, self._anim_action, self._natural_action,
            self._follow_action, self._through_action, self._hover_action, self._autostart_action, self._codex_follow_action,
            self._panel_action, *self._bridge_actions.values())]
        self._autostart_action.setChecked(settings.autostart)
        self._codex_follow_action.setChecked(settings.follow_codex_visibility)
        self._panel_action.setChecked(settings.show_task_panel)
        self._top_action.setChecked(settings.always_on_top)
        self._anim_action.setChecked(settings.animations_enabled)
        self._natural_action.setChecked(settings.natural_behavior)
        for key, action in self._extra_checks.items():
            blocker = QSignalBlocker(action)
            action.setChecked(getattr(settings, key))
        self._follow_action.setChecked(settings.mouse_follow)
        self._through_action.setChecked(settings.click_through)
        self._hover_action.setChecked(settings.hover_passthrough)
        for mode, act in self._bridge_actions.items():
            act.setChecked(settings.bridge_mode == mode)


def icon_from_frame(qimage) -> QIcon:
    pm = QPixmap.fromImage(qimage.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    return QIcon(pm)
