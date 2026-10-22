"""Change only our HWND's input bit, preserving its per-pixel layered surface."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import os

WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
GWL_EXSTYLE = -20


class WindowsInputTransparency:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.user.GetWindowThreadProcessId.restype = w.DWORD

    def set_enabled(self, handle: int, enabled: bool) -> None:
        owner = w.DWORD()
        if not self.user.GetWindowThreadProcessId(handle, ctypes.byref(owner)):
            raise ctypes.WinError(ctypes.get_last_error())
        if owner.value != os.getpid():
            raise OSError("Refusing to change another process's input transparency")
        ctypes.set_last_error(0)
        before = self.user.GetWindowLongPtrW(handle, GWL_EXSTYLE)
        if not before and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        # Qt owns WS_EX_LAYERED. Never clear/reapply it: that drops the backing
        # surface and can expose the rectangular canvas between native paints.
        if not before & WS_EX_LAYERED:
            raise OSError("Input transparency requires an existing layered surface")
        after = before | WS_EX_TRANSPARENT if enabled else before & ~WS_EX_TRANSPARENT
        if before != after:
            ctypes.set_last_error(0)
            previous = self.user.SetWindowLongPtrW(handle, GWL_EXSTYLE, after)
            if not previous and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
