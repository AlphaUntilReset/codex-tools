"""Single-press Ctrl drag must own the pointer, move, and release cleanly.

Injects events into isolated Qt/native fixture windows only. Does not move the
physical cursor or inject clicks into the desktop or daily pet.
"""
import ctypes
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QWidget
import pytest

from test_hover_passthrough import APP, pet, hover, through


def send_mouse(window, kind, point, button, buttons):
    local = point - window.pos()
    event = QMouseEvent(kind, QPointF(local), QPointF(point), button, buttons, Qt.ControlModifier)
    QCoreApplication.sendEvent(window, event)
    return event


def press(pet):
    window, cursor, modifiers = pet
    hover(pet)
    modifiers[0] = Qt.ControlModifier
    window._poll_hover()
    point = window.mapToGlobal(window.rect().center())
    cursor[0] = point
    event = send_mouse(window, QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton)
    return window, point, event


def test_single_press_owns_move_and_release_without_prior_click(pet, monkeypatch):
    window, cursor, modifiers = pet
    clicks = []
    monkeypatch.setattr(window._controller, 'on_click_character', lambda: clicks.append(True))
    origin = window.pos()
    window, point, event = press(pet)
    assert event.isAccepted(), 'handled press must not fall back to QWidget.ignore()'
    assert QWidget.mouseGrabber() is window, 'first press must capture future pointer events'
    modifiers[0] = Qt.NoModifier
    delta = QPoint(37, 24)
    cursor[0] = point + delta
    move = send_mouse(window, QEvent.MouseMove, point + delta, Qt.NoButton, Qt.LeftButton)
    assert move.isAccepted() and window.pos() == origin + delta
    assert window._drag_exceeded and not through(window)
    release = send_mouse(window, QEvent.MouseButtonRelease, point + delta, Qt.LeftButton, Qt.NoButton)
    assert release.isAccepted() and not window._drag_active
    assert QWidget.mouseGrabber() is not window and not clicks


def test_single_ctrl_click_still_waves_and_releases_capture(pet, monkeypatch):
    window, _, _ = pet
    clicks = []
    monkeypatch.setattr(window._controller, 'on_click_character', lambda: clicks.append(True))
    window, point, event = press(pet)
    send_mouse(window, QEvent.MouseButtonRelease, point, Qt.LeftButton, Qt.NoButton)
    assert clicks == [True] and QWidget.mouseGrabber() is not window


@pytest.mark.parametrize('finish', ['escape', 'hide', 'through', 'grab-lost'])
def test_drag_capture_is_released_on_cancellation(pet, finish):
    from dataclasses import replace
    from PySide6.QtGui import QKeyEvent
    window, point, event = press(pet)
    assert QWidget.mouseGrabber() is window
    if finish == 'escape':
        QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    elif finish == 'hide':
        window.hide()
    elif finish == 'through':
        window.apply_settings(replace(window._settings, click_through=True))
    else:
        window.releaseMouse()
        QCoreApplication.sendEvent(window, QEvent(QEvent.UngrabMouse))
    assert not window._drag_active and QWidget.mouseGrabber() is not window
    assert not window._drag_hold_timer.isActive()


@pytest.mark.parametrize('ctrl_already_polled', [False, True])
def test_windows_native_first_down_captures_moves_and_releases(pet, ctrl_already_polled):
    if os.name != 'nt' or APP.platformName() != 'windows':
        pytest.skip('Opt in with QT_QPA_PLATFORM=windows')
    from ctypes import wintypes as w
    window, cursor, modifiers = pet
    hover(pet)
    window._hover_timer.stop()  # deterministically exercise an unpolled Ctrl transition
    modifiers[0] = Qt.ControlModifier
    if ctrl_already_polled:
        window._poll_hover()
    APP.processEvents()
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.PostMessageW.restype = w.BOOL
    user.GetCapture.restype = w.HWND
    user.GetForegroundWindow.restype = w.HWND
    handle = int(window.winId())
    foreground = user.GetForegroundWindow()
    origin = window.pos()
    point = window.rect().center()
    ratio = window.devicePixelRatioF()
    def post(message, pos, keys):
        from PySide6.QtTest import QTest
        x, y = round(pos.x() * ratio), round(pos.y() * ratio)
        assert user.PostMessageW(handle, message, keys, (y & 65535) << 16 | (x & 65535))
        APP.processEvents()
        QTest.qWait(40)
    try:
        post(0x0201, point, 0x0009)  # WM_LBUTTONDOWN, MK_CONTROL|MK_LBUTTON
        assert window._drag_active and user.GetCapture() == handle
        post(0x0200, point + QPoint(38, 26), 0x0009)
        assert window._drag_exceeded and window.pos() != origin
        post(0x0202, point, 0x0008)
        assert not window._drag_active and user.GetCapture() != handle
        assert not through(window) and user.GetForegroundWindow() == foreground
    finally:
        window.releaseMouse()
