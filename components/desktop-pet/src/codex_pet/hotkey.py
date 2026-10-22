"""A reversible, non-exclusive Windows shortcut; no keyboard hook/injection."""
import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal


class GlobalHotkey(QObject, QAbstractNativeEventFilter):
    activated = Signal()
    ID = 0x4B50

    def __init__(self, app):
        QObject.__init__(self)
        QAbstractNativeEventFilter.__init__(self)
        self.app = app
        self.registered = False
        self.status = "未启用"
        app.installNativeEventFilter(self)

    def configure(self, enabled: bool):
        if enabled == self.registered:
            return
        self.close()
        if enabled and os.name == "nt":
            self.registered = bool(ctypes.windll.user32.RegisterHotKey(None, self.ID, 0x4000 | 2 | 1, ord("P")))
            self.status = "Ctrl+Alt+P" if self.registered else "快捷键已被占用，请使用托盘"

    def nativeEventFilter(self, event_type, message):
        if os.name == "nt" and self.registered:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == self.ID:
                self.activated.emit()
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            ctypes.windll.user32.UnregisterHotKey(None, self.ID)
        self.registered = False
        self.status = "未启用"
