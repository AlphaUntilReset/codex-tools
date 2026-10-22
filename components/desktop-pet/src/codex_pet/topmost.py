"""Repair native window order without showing hidden surfaces or taking focus."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
import logging
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

log = logging.getLogger("codex_pet.topmost")
POLL_SECONDS = 1.2
SWP_PRESERVE = 0x0001 | 0x0002 | 0x0010 | 0x0200  # size, position, focus, owner order
# Native menus, tooltips and the Windows shell must remain usable above the pet.
TRANSIENT_CLASSES = frozenset({"#32768", "tooltips_class32", "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow"})


@dataclass(frozen=True)
class WindowState:
    handle: int
    pid: int
    rect: tuple[int, int, int, int]
    visible: bool = True
    topmost: bool = True
    cloaked: bool = False
    minimized: bool = False
    kind: str = ""


def overlaps(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def repair_reason(targets, ordered, pid):
    """Windows in front of a surface matter even when its TOPMOST bit is set."""
    by_handle = {item.handle: index for index, item in enumerate(ordered)}
    own = [item for item in ordered if item.handle in targets and item.pid == pid
           and item.visible and not item.cloaked and not item.minimized]
    if not own:
        return None
    foreign = [item for item in ordered if item.pid != pid and item.visible
               and not item.cloaked and not item.minimized]
    if any(item.kind in TRANSIENT_CLASSES and any(overlaps(item.rect, pet.rect) for pet in own)
           for item in foreign):
        return None
    if any(not item.topmost for item in own):
        return "lost-topmost"
    for pet in own:
        if any(by_handle[item.handle] < by_handle[pet.handle] and overlaps(item.rect, pet.rect)
               for item in foreign):
            return "occluded"
    return None


class WindowsWindowOrder:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": ([self.callback, w.LPARAM], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsIconic": ([w.HWND], w.BOOL),
            "GetWindowLongW": ([w.HWND, ctypes.c_int], w.LONG),
            "GetWindowRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "GetClassNameW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "SetWindowPos": ([w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, w.UINT], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(self.user, name)
            method.argtypes, method.restype = args, result
        self.dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = w.LONG

    def snapshot(self):
        ordered = []

        @self.callback
        def observe(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                return True
            pid, rect, cloaked = w.DWORD(), w.RECT(), w.DWORD()
            if not self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
                return True
            if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return True
            self.dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            cls = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, cls, len(cls))
            ordered.append(WindowState(int(hwnd), pid.value,
                (rect.left, rect.top, rect.right, rect.bottom),
                topmost=bool(self.user.GetWindowLongW(hwnd, -20) & 8),
                cloaked=bool(cloaked.value), minimized=bool(self.user.IsIconic(hwnd)), kind=cls.value))
            return True

        if not self.user.EnumWindows(observe, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return ordered

    def set_band(self, handle, topmost):
        # Only the caller's own windows can ever be mutated, including after handle reuse.
        pid = w.DWORD()
        if not self.user.GetWindowThreadProcessId(handle, ctypes.byref(pid)) or pid.value != os.getpid():
            return False
        if not self.user.IsWindowVisible(handle) or self.user.IsIconic(handle):
            return False
        return bool(self.user.SetWindowPos(handle, w.HWND(-1 if topmost else -2),
                                          0, 0, 0, 0, SWP_PRESERVE))


class WindowsTopmostGuard:
    def __init__(self, surfaces, *, backend=None, clock=time.monotonic, pid=None):
        self.surfaces = tuple(surfaces)
        self.clock = clock
        self.pid = os.getpid() if pid is None else pid
        self.next_check = 0.0
        self.repairs = 0
        self._warned = False
        self.backend = backend
        if self.backend is None and os.name == "nt" and QGuiApplication.platformName() == "windows":
            self.backend = WindowsWindowOrder()

    def refresh(self):
        if self.backend is None or self.clock() < self.next_check:
            return False
        self.next_check = self.clock() + POLL_SECONDS
        # Preserve tray menus, settings dialogs, keyboard focus and user choice.
        if QApplication.activePopupWidget() or QApplication.activeModalWidget():
            return False
        targets = [int(surface.winId()) for surface in self.surfaces
                   if surface.isVisible() and surface.windowFlags() & Qt.WindowStaysOnTopHint]
        if not targets:
            return False
        try:
            snapshot = self.backend.snapshot()
            reason = repair_reason(targets, snapshot, self.pid)
            if not reason:
                return False
            # Demote the group first, then promote in owner -> child order. Demoting
            # an owned panel after promoting its owner would undo the pet's repair.
            own = {item.handle for item in snapshot if item.pid == self.pid
                   and item.visible and not item.cloaked and not item.minimized}
            targets = [handle for handle in targets if handle in own]
            success = True
            for band in (False, True):
                for handle in targets:
                    if not self.backend.set_band(handle, band):
                        success = False
            if not success:
                raise OSError("Window-order repair was refused")
            self.repairs += 1
            self._warned = False
            log.info("已恢复桌宠窗口置顶（%s，%d 个可见窗口）", reason, len(targets))
            return True
        except OSError as error:
            if not self._warned:
                log.warning("桌宠窗口置顶恢复暂未完成，将自动重试：%s", error)
                self._warned = True
            return False
