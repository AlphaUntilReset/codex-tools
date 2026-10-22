"""Hide for a foreground fullscreen app without changing the user's visibility choice."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w

from PySide6.QtCore import QObject, QTimer


def covers_monitor(client, monitor, captioned=False):
    """Use client pixels, not the oversized invisible frame of a maximized window."""
    if captioned:
        return False
    left, top, right, bottom = client
    ml, mt, mr, mb = monitor
    return (right > left and bottom > top and left <= ml+2 and top <= mt+2
            and right >= mr-2 and bottom >= mb-2)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT),
                ("rcWork", w.RECT), ("dwFlags", w.DWORD)]


class WindowsFullscreenProbe:
    """Read geometry only; no page titles, video contents or process injection."""
    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
        if self.api is None:
            return
        signatures = {
            "GetForegroundWindow": ([], w.HWND),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetClassNameW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowLongW": ([w.HWND, ctypes.c_int], w.LONG),
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "MonitorFromWindow": ([w.HWND, w.DWORD], w.HANDLE),
            "GetMonitorInfoW": ([w.HANDLE, ctypes.POINTER(MONITORINFO)], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.api, name)
            method.argtypes, method.restype = args, result

    def __call__(self, pet_hwnd):
        if self.api is None:
            return False
        u = self.api
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return None  # Transient focus change: retain the previous decision.
        if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return False
        process = w.DWORD()
        if not u.GetWindowThreadProcessId(hwnd, ctypes.byref(process)):
            return None
        if process.value == os.getpid():
            return False
        cls = ctypes.create_unicode_buffer(256)
        if not u.GetClassNameW(hwnd, cls, len(cls)):
            return None
        if cls.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
            return False
        monitor = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if monitor != u.MonitorFromWindow(pet_hwnd, 2):
            return False
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        rect = w.RECT()
        if not u.GetMonitorInfoW(monitor, ctypes.byref(info)) or not u.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        start, end = w.POINT(rect.left, rect.top), w.POINT(rect.right, rect.bottom)
        if not u.ClientToScreen(hwnd, ctypes.byref(start)) or not u.ClientToScreen(hwnd, ctypes.byref(end)):
            return None
        screen = info.rcMonitor
        captioned = (u.GetWindowLongW(hwnd, -16) & 0x00C00000) == 0x00C00000
        return covers_monitor((start.x, start.y, end.x, end.y),
                              (screen.left, screen.top, screen.right, screen.bottom), captioned)


class CodexProcessFollower(QObject):
    """随官方 Codex 桌面版进程显隐（方案：显隐跟随，无强杀）。

    官方全部进程退出且连续两次探测确认（约 10 秒，防官方崩溃重启抖动）
    → 宠物隐藏到托盘；官方再次出现 → 宠物现身（不抢焦点，全屏场景仍让路）。
    只在探测边沿动作：持续"在"时不干预手动隐藏，持续"不在"时不干预手动显示。
    """

    POLL_MS = 5000
    CONFIRM_N = 2

    def __init__(self, visibility, probe=None, poll_ms=POLL_MS):
        super().__init__(visibility)
        from codex_pet.bridge.codex_local import codex_desktop_running
        self._visibility = visibility
        self._probe = probe if probe is not None else codex_desktop_running
        self._last_seen: bool | None = None
        self._misses = 0
        self.timer = QTimer(self, interval=poll_ms)
        self.timer.timeout.connect(self._tick)

    def start(self):
        # 开启时对齐一次当前状态；此后仅边沿驱动。
        self._last_seen = bool(self._probe())
        self._misses = 0
        if self._last_seen:
            self._visibility.request_show()
        else:
            self._visibility.request_hide()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def _tick(self):
        seen = bool(self._probe())
        if seen:
            self._misses = 0
            if self._last_seen is False:
                self._visibility.request_show()
        elif self._last_seen:
            self._misses += 1
            if self._misses >= self.CONFIRM_N:
                self._visibility.request_hide()
                self._last_seen = False
                return
        self._last_seen = seen if seen or self._last_seen is False else self._last_seen


class PetVisibility(QObject):
    """The timer keeps running while the pet (and its animation timers) is hidden."""
    def __init__(self, window, probe=None, *, surfaces=(), topmost=None):
        super().__init__(window)
        self.window = window
        self.probe = probe if probe is not None else WindowsFullscreenProbe()
        from codex_pet.topmost import WindowsTopmostGuard
        self.topmost = topmost if topmost is not None else WindowsTopmostGuard((window, *surfaces))
        self.requested_visible = False
        self.fullscreen = False
        self._clear_samples = 0
        self.timer = QTimer(self, interval=400)
        self.timer.timeout.connect(self.refresh)

    def start(self, visible):
        self.requested_visible = visible
        self.refresh()
        self.timer.start()

    def stop(self):
        self.timer.stop()

    def refresh(self):
        result = self.probe(int(self.window.winId()))
        if result is True:
            self.fullscreen = True
            self._clear_samples = 0
        elif result is False:
            self._clear_samples += 1
            if self._clear_samples >= 2:
                self.fullscreen = False
        else:
            self._clear_samples = 0
        self._apply()
        if result is False and self._clear_samples >= 2 and self.window.isVisible():
            self.topmost.refresh()

    def _apply(self):
        show = self.requested_visible and not self.fullscreen
        if self.window.isVisible() != show:
            self.window.setVisible(show)

    def request_show(self):
        self.requested_visible = True
        self.refresh()

    def request_hide(self):
        self.requested_visible = False
        self.refresh()

    def toggle(self):
        self.requested_visible = not self.requested_visible
        self.refresh()
