"""应用入口：单实例、缓存自举、桥接装配、托盘与生命周期。"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from codex_pet import __version__, paths, diagnostics
from codex_pet.animation import AnimationCatalog, AnimationPlayer
from codex_pet.assets import AssetPaths, build_frame_cache, cache_is_fresh
from codex_pet.bridge import BridgeState, Connection, TaskStatus
from codex_pet.bridge.codex_local import CodexLocalBridge
from codex_pet.bridge.demo import DemoBridge
from codex_pet.bridge.disabled import DisabledBridge
from codex_pet.bridge.plan_usage import PlanUsage, PlanUsageProvider, usage_text
from codex_pet.controller import PetController
from codex_pet.custom_assets import AssetPackError, import_single_image, load_asset_pack, select_asset_pack
from codex_pet.renderer import FrameRenderer
from codex_pet.settings import Settings, load_settings, save_settings
from codex_pet.tray import (
    DebugDialog,
    SettingsDialog,
    TrayController,
    bridge_status_text,
    icon_from_frame,
)
from codex_pet.window import PetWindow
from codex_pet.task_panel import TaskPanel
from codex_pet.bridge.codex_local import open_codex_desktop, open_codex_task
from codex_pet.hotkey import GlobalHotkey
from codex_pet.visibility import PetVisibility

log = logging.getLogger("codex_pet.app")

SINGLE_INSTANCE_KEY = "CodexToolsPet.SingleInstance.v1"


def tray_icon_safely(renderer: FrameRenderer) -> QIcon:
    """托盘图标取帧失败（如帧缓存 CRC 校验后仍损坏）时降级为空白图标，不阻断启动。"""
    try:
        return icon_from_frame(renderer.full_frame(0, 0))
    except FileNotFoundError:
        log.warning("托盘图标帧加载失败，使用空白图标（帧缓存可能损坏，可重建缓存恢复）")
        return QIcon()


def setup_logging() -> None:
    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "codex_pet.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if getattr(sys, "frozen", False) and "--console" in sys.argv:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)


def bootstrap_frame_cache() -> Path:
    """选择可用的帧缓存目录；必要时从图集构建（首次运行）。"""
    manifest_path = paths.resource_base_dir() / "assets" / "codex_pet" / "source-manifest.json"
    from codex_pet.assets import load_json

    manifest = load_json(manifest_path)
    candidates = [
        paths.frame_cache_dir(),
        paths.resource_base_dir() / "cache" / "frames",  # 发布包预构建缓存
    ]
    for cache_dir in candidates:
        asset_paths = AssetPaths(
            webp=paths.resource_base_dir() / "assets" / "codex_pet" / "spritesheet-hd.webp",
            manifest=manifest_path,
            animations=paths.resource_base_dir() / "assets" / "codex_pet" / "animations.reference.json",
            cache_dir=cache_dir,
        )
        try:
            if cache_is_fresh(asset_paths):
                return cache_dir
        except (OSError, ValueError, KeyError):
            continue
    # 现有缓存都不可用：在用户目录重建
    asset_paths = AssetPaths(
        webp=paths.resource_base_dir() / "assets" / "codex_pet" / "spritesheet-hd.webp",
        manifest=manifest_path,
        animations=paths.resource_base_dir() / "assets" / "codex_pet" / "animations.reference.json",
        cache_dir=paths.frame_cache_dir(),
    )
    started = time.monotonic()
    report = build_frame_cache(asset_paths, verify_sha=True)
    log.info(
        "帧缓存构建完成: %d 格, 耗时 %.1fs",
        len(report["cells"]),
        time.monotonic() - started,
    )
    return paths.frame_cache_dir()


def bridge_state_indicates_work(state: BridgeState) -> bool:
    """真实桥有任一任务在运行才算"Codex 工作中"（额度正在消耗）。

    不用 aggregate_status()：未读完成（READY）在显示上优先于运行中，
    但"一个任务待读 + 另一个在跑"时额度仍在消耗，必须保持高频。
    演示事件不驱动真实网络频率。
    """
    return (
        not state.is_demo
        and state.connection == Connection.CONNECTED
        and any(t.status is TaskStatus.RUNNING for t in state.tasks.values())
    )


class CodexToolsApp:
    def __init__(self, qt_app: QApplication, args: argparse.Namespace):
        self.qt_app = qt_app
        self.args = args
        self.data_dir = paths.user_data_dir()
        self.settings, note = load_settings(self.data_dir)
        if note:
            log.warning("%s", note)
        self._server: QLocalServer | None = None
        self._bridge = None
        self._dialogs: list = []

        resource_base = paths.resource_base_dir()
        custom_manifest = select_asset_pack(paths.app_base_dir(), resource_base,
                                            getattr(args, "asset_pack", None))
        if custom_manifest is not None:
            pack = load_asset_pack(custom_manifest)
            self.catalog = pack.catalog
            self.renderer = FrameRenderer(custom_manifest.parent, extra_frames=pack.frames)
            log.info("使用自备素材包: %s", custom_manifest.name)
        else:
            cache_dir = bootstrap_frame_cache()
            self.catalog = AnimationCatalog.load(resource_base / "assets" / "codex_pet" / "animations.reference.json")
            # Private original artwork keeps every existing verification gate.
            self.renderer = FrameRenderer(cache_dir, look_dir=resource_base / "assets/codex_pet/look-v2",
                baseline_profile=resource_base / "resources/baseline-047/manifest.json",
                baseline_display_cache=resource_base / ("cache/baseline-047-frames" if getattr(sys,'frozen',False)
                                                        else "build/baseline-047-frames"),
                consistent_eye_gaze=True)
        self.player = AnimationPlayer(self.catalog)
        self.controller = PetController(self.catalog, self.player)
        self.window = PetWindow(self.catalog, self.player, self.controller, self.renderer, self.settings)
        self.task_panel = TaskPanel(self.window)
        self.task_panel.apply_settings(self.settings)
        self.task_panel.open_task.connect(self.open_task)
        self.task_panel.preferences_changed.connect(self._panel_preferences_changed)
        self.visibility = PetVisibility(self.window, surfaces=(self.task_panel, self.task_panel.details))
        from codex_pet.visibility import CodexProcessFollower
        self.codex_follower = CodexProcessFollower(self.visibility)
        qt_app.aboutToQuit.connect(self.visibility.stop)
        self.hotkey = GlobalHotkey(qt_app)
        self.hotkey.activated.connect(self.toggle_visibility)
        self.hotkey.configure(self.settings.global_hotkey)
        qt_app.aboutToQuit.connect(self.hotkey.close)

        self.window.settings_changed.connect(self._on_settings_changed)
        if args.reset_position:
            self.settings.pos_x = self.settings.pos_y = None

        available_actions=set(self.catalog.animations)
        if self.renderer.baseline_available_groups is not None:
            available_actions &= self.renderer.baseline_available_groups
        self.tray = TrayController(tray_icon_safely(self.renderer), self.settings, available_actions=available_actions)
        self.tray.toggle_visibility.connect(self.toggle_visibility)
        self.tray.reset_position.connect(self.reset_position)
        self.tray.apply_settings.connect(self.apply_settings)
        self.tray.manual_action.connect(self.controller.manual_action)
        self.tray.set_bridge_mode.connect(self.set_bridge_mode)
        self.tray.quit_app.connect(self.quit)
        self.tray.open_debug.connect(self.open_debug)
        self.tray.open_settings.connect(self.open_settings)
        self.tray.show()
        self.window.context_menu_requested.connect(self.tray.pet_menu().popup)

        # 桥启动会同步回调 _on_bridge_state，余额提供器与这两个缓存必须先初始化。
        self._last_state: BridgeState | None = None
        self._last_usage: PlanUsage | None = None
        # 套餐余额：独立于桥模式的只读查询（auth.json 缺失时如实显示未登录）。
        # 回调里会用桥状态驱动轮询频率，故必须先于桥创建。
        self.usage_provider = PlanUsageProvider()
        self.usage_provider.updated.connect(self._on_plan_usage)
        self.usage_provider.start()
        self._apply_bridge_mode(self.settings.bridge_mode)
        # 仅修复已有受管入口的路径；不能凭旧 settings 重建入口或覆盖明确关闭意图。
        if self.settings.autostart:
            from codex_pet.autostart import set_autostart
            set_autostart(True, explicit=False)
        self.window.restore_position()
        self.visibility.start(self.settings.show_on_start)
        if self.settings.follow_codex_visibility:
            self.codex_follower.start()

    # ---------- 设置 ----------

    def _on_settings_changed(self, settings: Settings) -> None:
        self.settings = settings
        save_settings(settings, self.data_dir)
        self.tray.sync_from_settings(settings)
        self.task_panel.apply_settings(settings)
        self.hotkey.configure(settings.global_hotkey)

    def _panel_preferences_changed(self, collapsed: bool, attention_only: bool) -> None:
        self.settings.task_panel_collapsed = collapsed
        self.settings.attention_only = attention_only
        self._on_settings_changed(self.settings)

    def apply_settings(self, settings: Settings) -> None:
        if settings.bridge_mode != self.settings.bridge_mode:
            self._apply_bridge_mode(settings.bridge_mode)
        if settings.autostart != self.settings.autostart:
            from codex_pet.autostart import set_autostart
            if not set_autostart(settings.autostart):
                log.warning("开机自启设置未完成（注册表访问失败或存在自定义启动命令）")
                settings.autostart = self.settings.autostart  # 保持原值，避免假成功
        if settings.follow_codex_visibility != self.settings.follow_codex_visibility:
            if settings.follow_codex_visibility:
                self.codex_follower.start()
            else:
                self.codex_follower.stop()
        self.window.apply_settings(settings)
        self._on_settings_changed(settings)

    # ---------- 桥接 ----------

    def _apply_bridge_mode(self, mode: str) -> None:
        if self._bridge is not None:
            self._bridge.stop()
        if mode == "demo":
            self._bridge = DemoBridge()
            log.info("桥接模式: demo（演示，非真实联动）")
        elif mode == "codex-local":
            self._bridge = CodexLocalBridge()
            log.info("桥接模式: codex-local（只读尽力同步，非官方接口）")
        else:
            self._bridge = DisabledBridge()
            log.info("桥接模式: disabled（独立模式）")
        self._bridge.set_callback(self._on_bridge_state)
        self._bridge.start()

    def open_task(self, task_id: str) -> None:
        if not open_codex_task(task_id):
            self.task_panel.show_open_error()
            log.warning("系统未能打开任务入口")

    def set_bridge_mode(self, mode: str) -> None:
        if mode not in ("disabled", "demo", "codex-local"):
            mode = "disabled"
        self.settings.bridge_mode = mode
        self._apply_bridge_mode(mode)
        self._on_settings_changed(self.settings)

    def _on_bridge_state(self, state: BridgeState) -> None:
        if state.is_demo and state.connection == Connection.CONNECTED:
            log.info("演示事件: tasks=%s", {k: v.status.value for k, v in state.tasks.items()})
        previous = self.controller.task_status
        self.controller.apply_bridge_state(state)
        current = self.controller.task_status
        if state.mode != "disabled" and current is not previous:
            log.info(
                "任务状态: %s → %s（连接=%s, 任务数=%d）",
                previous.value,
                current.value,
                state.connection.value,
                len(state.tasks),
            )
        self._last_state = state
        self._refresh_tray_tooltip()
        self.task_panel.set_state(state)
        # Codex 有任务运行时余额轮询升高频，空闲回低频（桥每 2 秒回调一次，
        # set_work_active 内部按边沿去重，不会重复触发取数）。
        self.usage_provider.set_work_active(bridge_state_indicates_work(state))

    def _on_plan_usage(self, usage: PlanUsage) -> None:
        self._last_usage = usage
        self.task_panel.set_plan_usage(usage)
        self._refresh_tray_tooltip()

    def _refresh_tray_tooltip(self) -> None:
        base = "桌宠桌面宠物"
        if self._last_state is not None:
            base = bridge_status_text(self._last_state) or base
        line = usage_text(self._last_usage)
        self.tray.setToolTip(base + (f"\n{line}" if line else ""))

    # ---------- 托盘动作 ----------

    def toggle_visibility(self) -> None:
        self.visibility.toggle()

    def reset_position(self) -> None:
        self.settings.pos_x = self.settings.pos_y = None
        self.window.reset_position()
        self.visibility.request_show()
        self.window._save_position()
        self._on_settings_changed(self.settings)

    def open_debug(self) -> None:
        dialog = DebugDialog(self.window.debug_info)
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda _=0, d=dialog: self._dialogs.remove(d))
        dialog.show()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings,available_actions=self.catalog.animations)
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda _=0, d=dialog: self._dialogs.remove(d))
        if dialog.exec():
            self.apply_settings(dialog.result_settings(self.settings))

    # ---------- 单实例与退出 ----------

    def listen_single_instance(self) -> bool:
        self._server = QLocalServer()
        self._server.listen(SINGLE_INSTANCE_KEY)
        self._server.newConnection.connect(self._on_new_connection)
        return True

    def _on_new_connection(self) -> None:
        conn = self._server.nextPendingConnection()
        if conn is not None:
            conn.waitForReadyRead(200)
            message = bytes(conn.readAll()).decode("utf-8", "replace").strip()
            conn.disconnectFromServer()
            log.info("收到二次启动请求: %s", message or "(空)")
            self.visibility.request_show()

    def quit(self) -> None:
        diagnostics.event(exit_intent="user-quit")
        log.info("退出")
        self.codex_follower.stop()
        self.visibility.stop()
        self.usage_provider.stop()
        if self._bridge is not None:
            self._bridge.stop()
        save_settings(self.settings, self.data_dir)
        self.window.hide()
        self.hotkey.close()
        if self._server is not None:
            self._server.close()
        self.qt_app.quit()


def notify_existing_instance() -> bool:
    """已有实例运行时通知其显示，返回 True。"""
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_INSTANCE_KEY)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show\n")
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="独立高清桌宠桌面宠物")
    parser.add_argument("--reset-position", action="store_true", help="重置窗口位置后启动")
    parser.add_argument("--console", action="store_true", help="冻结包中附带控制台日志")
    parser.add_argument("--asset-pack", type=Path, help="自备素材包的 pack.json；默认 user-assets/pet/pack.json")
    parser.add_argument(
        "--launch-codex",
        action="store_true",
        help="启动官方 Codex 桌面版并拉起宠物（用于桌面快捷方式联动）",
    )
    args = parser.parse_args(argv)

    setup_logging()
    diagnostics.install_qt_logging()
    log.info(
        "启动 CodexToolsPet %s (Python %s)%s",
        __version__,
        sys.version.split()[0],
        " [随 Codex 启动]" if args.launch_codex else "",
    )

    if args.launch_codex:
        # 无论宠物是否已在运行，都先启动/激活官方 Codex
        if not open_codex_desktop():
            log.warning("随 Codex 启动：未能定位官方 Codex 入口")

    qt_app = QApplication(argv or [])
    qt_app.setApplicationName("CodexToolsPet")
    qt_app.setQuitOnLastWindowClosed(False)  # 隐藏到托盘时不退出
    qt_app.commitDataRequest.connect(lambda _: diagnostics.event(session_end_requested=True))

    if notify_existing_instance():
        diagnostics.event(exit_intent="duplicate-instance")
        log.info("已有实例，通知显示后退出")
        return 0

    default_manifest = paths.app_base_dir() / "user-assets/pet/pack.json"
    if (args.asset_pack is None and not default_manifest.exists()
            and not (paths.resource_base_dir() / "assets/codex_pet/source-manifest.json").exists()):
        answer = QMessageBox.question(
            None, "导入自备素材", "开源版不包含角色美术资产。\n"
            "是否选择一张自己有权使用的透明 PNG？\n"
            "图片将复制为 user-assets/pet/idle.png，之后可添加动画帧。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            diagnostics.event(exit_intent="asset-import-cancelled")
            return 2
        filename, _ = QFileDialog.getOpenFileName(None, "选择自备透明 PNG", "", "PNG (*.png)")
        if not filename:
            diagnostics.event(exit_intent="asset-import-cancelled")
            return 2
        try:
            import_single_image(Path(filename), default_manifest.parent)
        except AssetPackError as error:
            QMessageBox.critical(None, "素材导入失败", str(error))
            diagnostics.event(exit_intent="invalid-assets")
            return 2

    try:
        app = CodexToolsApp(qt_app, args)
    except AssetPackError as error:
        log.error("素材包未就绪: %s", error)
        QMessageBox.critical(None, "请准备自备素材", str(error))
        diagnostics.event(exit_intent="missing-or-invalid-assets")
        return 2
    app.listen_single_instance()
    qt_app.aboutToQuit.connect(lambda: diagnostics.event(qt_about_to_quit=True))
    diagnostics.event(stage="ready")
    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
