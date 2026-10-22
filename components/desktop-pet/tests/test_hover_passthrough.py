"""Hover recovery, interaction escape, settings migration and native hit routing.

Normal runs use offscreen. Opt in to the isolated native fixture using
QT_QPA_PLATFORM=windows; it never moves the user's cursor or sends real clicks.
"""
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget
from codex_pet.animation import AnimationPlayer
from codex_pet.controller import PetController
from codex_pet.custom_assets import load_asset_pack
from codex_pet.settings import Settings, load_settings, save_settings
from codex_pet.tray import SettingsDialog, TrayController
from codex_pet.window import HOVER_OPACITY, PetWindow
from PySide6.QtGui import QIcon

APP = QApplication.instance() or QApplication([])


class SolidRenderer:
    """Bounded opaque character with transparent canvas margins."""
    def alpha_at(self, row, col, x, y):
        return 255 if 100 <= x < 668 and 100 <= y < 732 else 0

    def clear_scaled(self):
        pass

    def pixmap(self, row, col, size, ratio):
        image = QImage(768, 832, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        from PySide6.QtGui import QPainter
        painter = QPainter(image)
        painter.fillRect(100, 100, 568, 632, Qt.blue)
        painter.end()
        pixmap = QPixmap.fromImage(image.scaled(size))
        pixmap.setDevicePixelRatio(ratio)
        return pixmap


@pytest.fixture
def pet(monkeypatch, tmp_path):
    image = Image.new("RGBA", (768, 832), (0, 0, 0, 0))
    image.paste((0, 0, 255, 255), (100, 100, 668, 732))
    image.save(tmp_path / "idle.png")
    manifest = tmp_path / "pack.json"
    manifest.write_text(json.dumps({"schema_version": 1, "animations": {
        "idle": {"frames": ["idle.png"], "durations_ms": [1500]}}}), encoding="utf-8")
    catalog = load_asset_pack(manifest).catalog
    player = AnimationPlayer(catalog)
    controller = PetController(catalog, player)
    window = PetWindow(catalog, player, controller, SolidRenderer(),
                       Settings(animations_enabled=False, mouse_follow=False, natural_behavior=False))
    cursor = [QPoint(-20000, -20000)]
    modifiers = [Qt.NoModifier]
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: cursor[0]))
    monkeypatch.setattr(QGuiApplication, "queryKeyboardModifiers", staticmethod(lambda: modifiers[0]))
    window.move(40, 40)
    window.show()
    APP.processEvents()
    yield window, cursor, modifiers
    window.close()
    window.deleteLater()
    APP.processEvents()


def through(window):
    if os.name == "nt" and APP.platformName() == "windows":
        from ctypes import wintypes as w
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
        user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        return bool(user.GetWindowLongPtrW(int(window.winId()), -20) & 0x20)
    return bool(window.windowHandle().flags() & Qt.WindowTransparentForInput)


def hover(pet):
    window, cursor, modifiers = pet
    cursor[0] = window.mapToGlobal(window.rect().center())
    window._poll_hover()
    return window


def test_body_fades_and_passes_input_then_restores_without_leave_event(pet):
    window, cursor, _ = pet
    assert window._hover_timer.isActive()  # independent of animation/gaze timers
    assert not window._frame_timer.isActive() and not window._follow_timer.isActive()
    window = hover(pet)
    assert window._hover_active and through(window)
    assert window.windowOpacity() == 1.0
    assert window.grab().toImage().pixelColor(window.width() // 2, window.height() // 2).alpha() == pytest.approx(255 * HOVER_OPACITY, abs=1)
    cursor[0] = window.mapToGlobal(QPoint(2, 2))  # transparent canvas is not body
    window._poll_hover()
    assert not window._hover_active and not through(window)
    assert window.windowOpacity() == 1.0


def test_ctrl_and_drag_suspend_hover_and_permanent_through_is_preserved(pet):
    window, _, modifiers = pet
    hover(pet)
    modifiers[0] = Qt.ControlModifier
    window._poll_hover()
    assert not through(window) and window.windowOpacity() == 1.0
    modifiers[0] = Qt.NoModifier
    window._drag_active = True
    window._poll_hover()
    assert not through(window)
    window._drag_active = False
    window._poll_hover()
    assert through(window)
    window.apply_settings(replace(window._settings, click_through=True))
    modifiers[0] = Qt.ControlModifier
    window._poll_hover()
    assert through(window) and window.windowOpacity() == 1.0


def test_disable_hide_show_and_settings_reapplication_recover(pet):
    window = hover(pet)
    for _ in range(3):
        window.apply_settings(replace(window._settings))
        assert window.isVisible() and through(window)
    window.apply_settings(replace(window._settings, hover_passthrough=False))
    assert not through(window) and window.windowOpacity() == 1.0
    assert not window._hover_timer.isActive()
    window.apply_settings(replace(window._settings, hover_passthrough=True, reduced_motion=True))
    assert through(window) and window._hover_timer.isActive()
    window.hide()
    assert not through(window) and window.windowOpacity() == 1.0
    assert not window._hover_timer.isActive()
    window.show()
    assert through(window) and window._hover_timer.isActive()


def test_open_menu_or_dialog_temporarily_restores_interaction(pet, monkeypatch):
    window = hover(pet)
    monkeypatch.setattr(window, "_has_open_menu_or_dialog", lambda: True)
    window._poll_hover()
    assert not through(window) and window.windowOpacity() == 1.0


def test_timer_recovers_without_any_mouse_event_and_ctrl_allows_drag(pet):
    from PySide6.QtTest import QTest
    window, cursor, modifiers = pet
    hover(pet)
    cursor[0] = window.mapToGlobal(QPoint(-10, -10))
    QTest.qWait(100)
    assert not through(window) and window.windowOpacity() == 1.0
    cursor[0] = window.mapToGlobal(window.rect().center())
    modifiers[0] = Qt.ControlModifier
    window._poll_hover()
    QTest.mousePress(window, Qt.LeftButton, Qt.ControlModifier, window.rect().center())
    assert window._drag_active
    modifiers[0] = Qt.NoModifier
    window._poll_hover()
    assert not through(window)  # releasing Ctrl during an established drag is safe
    window._drag_exceeded = True
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, window.rect().center())
    assert not window._drag_active


def test_missing_frame_recovers_full_opacity(pet, monkeypatch):
    window = hover(pet)
    def missing(*args):
        raise FileNotFoundError("isolated missing-frame fixture")
    monkeypatch.setattr(window._renderer, "alpha_at", missing)
    window._poll_hover()
    assert not through(window) and window.windowOpacity() == 1.0


def test_repeated_fades_keep_empty_canvas_transparent_and_frames_live(pet, monkeypatch):
    window, _, modifiers = pet
    from PySide6.QtCore import QSize
    source = window._renderer.pixmap(0, 0, QSize(window.width(), window.height()), 1.0)
    original = source.toImage().copy()
    frames = []

    def frame(*args):
        frames.append(args[:2])
        return source

    monkeypatch.setattr(window._renderer, "pixmap", frame)
    for _ in range(24):
        hover(pet)
        faded = window.grab().toImage()
        assert faded.pixelColor(0, 0).alpha() == 0
        assert faded.pixelColor(faded.width() // 2, faded.height() // 2).alpha() == pytest.approx(255 * HOVER_OPACITY, abs=1)
        modifiers[0] = Qt.ControlModifier
        window._poll_hover()
        opaque = window.grab().toImage()
        assert opaque.pixelColor(0, 0).alpha() == 0
        assert opaque.pixelColor(opaque.width() // 2, opaque.height() // 2).alpha() == 255
        assert window.windowOpacity() == 1.0 and not through(window)
        modifiers[0] = Qt.NoModifier
    assert len(frames) >= 48 and source.toImage() == original


def test_hover_does_not_call_native_window_opacity_or_qwindow_flags(pet, monkeypatch):
    if os.name != "nt" or APP.platformName() != "windows":
        pytest.skip("Opt in with QT_QPA_PLATFORM=windows")
    window, _, modifiers = pet

    def forbidden(*args):
        pytest.fail("Hover must not reset native opacity or Qt window flags")

    monkeypatch.setattr(window, "setWindowOpacity", forbidden)
    monkeypatch.setattr(window.windowHandle(), "setFlag", forbidden)
    hover(pet)
    modifiers[0] = Qt.ControlModifier
    window._poll_hover()
    assert not through(window)


def test_old_settings_default_and_new_toggle_roundtrip(tmp_path):
    (tmp_path / "settings.json").write_text('{"canvas_height_dip": 480}', encoding="utf-8")
    settings, _ = load_settings(tmp_path)
    assert settings.hover_passthrough and settings.canvas_height_dip == 480
    settings.hover_passthrough = False
    save_settings(settings, tmp_path)
    assert not load_settings(tmp_path)[0].hover_passthrough
    tray = TrayController(QIcon(), Settings())
    emitted = []
    tray.apply_settings.connect(emitted.append)
    tray._hover_action.setChecked(False)
    assert len(emitted) == 1 and not emitted[0].hover_passthrough
    tray.sync_from_settings(Settings())
    assert tray._hover_action.isChecked() and len(emitted) == 1
    dialog = SettingsDialog(Settings())
    dialog._checkboxes["hover_passthrough"].setChecked(False)
    assert not dialog.result_settings(Settings()).hover_passthrough
    dialog.deleteLater()
    tray.deleteLater()


def test_windows_native_target_routes_to_underlying_window_and_recovers(pet):
    if os.name != "nt" or APP.platformName() != "windows":
        pytest.skip("Opt in with QT_QPA_PLATFORM=windows")
    from ctypes import wintypes as w
    window, _, modifiers = pet
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.WindowFromPoint.argtypes = [w.POINT]
    user.WindowFromPoint.restype = w.HWND
    user.GetForegroundWindow.restype = w.HWND
    under = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    under.setAttribute(Qt.WA_ShowWithoutActivating)
    under.setStyleSheet("background: white")
    under.setGeometry(window.geometry())
    under.show()
    window.raise_()
    APP.processEvents()
    foreground = user.GetForegroundWindow()
    geometry, handle = window.geometry(), int(window.winId())
    # Do not move the physical cursor. Query the OS hit target at a known body pixel.
    native_point = w.POINT()
    user.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    rect = w.RECT()
    assert user.GetWindowRect(handle, ctypes.byref(rect))
    native_point.x = (rect.left + rect.right) // 2
    native_point.y = (rect.top + rect.bottom) // 2
    try:
        target = user.WindowFromPoint(native_point)
        if target != handle:
            user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
            kind = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(target, kind, len(kind))
            if kind.value == "LockScreenBackstopFrame":
                pytest.skip("Windows session is locked; OS hit routing is unavailable on the application desktop")
        assert user.WindowFromPoint(native_point) == handle
        for _ in range(10):
            hover(pet)
            APP.processEvents()
            assert user.WindowFromPoint(native_point) == int(under.winId())
            modifiers[0] = Qt.ControlModifier
            window._poll_hover()
            APP.processEvents()
            get_style = user.GetWindowLongPtrW
            get_style.argtypes = [w.HWND, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            assert user.WindowFromPoint(native_point) == handle, {
                "style": hex(get_style(handle, -20)), "flags": int(window.windowHandle().flags()),
                "hover": window._hover_active, "visible": window.isVisible(), "opacity": window.windowOpacity(),
                "current_handle": int(window.winId()), "foreground": user.GetForegroundWindow(),
            }
            modifiers[0] = Qt.NoModifier
        assert window.isVisible() and window.geometry() == geometry and int(window.winId()) == handle
        assert user.GetForegroundWindow() == foreground
    finally:
        under.close()
        under.deleteLater()


def test_native_hover_never_resets_layered_surface(pet):
    if os.name != "nt" or APP.platformName() != "windows":
        pytest.skip("Opt in with QT_QPA_PLATFORM=windows")
    from ctypes import wintypes as w
    from PySide6.QtCore import QAbstractNativeEventFilter
    from PySide6.QtTest import QTest

    window, _, modifiers = pet
    handle, geometry = int(window.winId()), window.geometry()

    class Style(ctypes.Structure):
        _fields_ = [("old", w.DWORD), ("new", w.DWORD)]

    class ObserveStyles(QAbstractNativeEventFilter):
        def __init__(self):
            super().__init__()
            self.transitions = []

        def nativeEventFilter(self, kind, pointer):
            if kind == b"windows_generic_MSG":
                message = w.MSG.from_address(int(pointer))
                if message.hWnd == handle and message.message == 0x007D and ctypes.c_ssize_t(message.wParam).value == -20:
                    style = Style.from_address(message.lParam)
                    self.transitions.append((style.old, style.new))
            return False, 0

    observer = ObserveStyles()
    APP.installNativeEventFilter(observer)
    try:
        for _ in range(12):
            hover(pet)
            QTest.qWait(20)
            modifiers[0] = Qt.ControlModifier
            window._poll_hover()
            QTest.qWait(20)
            modifiers[0] = Qt.NoModifier
        assert observer.transitions, "The native style listener must observe actual transitions"
        forbidden = [(hex(a), hex(b)) for a, b in observer.transitions if (a ^ b) & ~0x20]
        assert not forbidden, f"Hover reset native styles beyond WS_EX_TRANSPARENT: {forbidden}"
        assert all(a & 0x80000 and b & 0x80000 for a, b in observer.transitions)
        assert int(window.winId()) == handle and window.geometry() == geometry
    finally:
        APP.removeNativeEventFilter(observer)
