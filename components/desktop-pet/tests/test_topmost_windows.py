"""Opt-in real Windows regression; normal test runs use offscreen and skip it.

QT_QPA_PLATFORM=windows python -m pytest tests/test_topmost_windows.py -q
Creates only small, non-activating fixture windows; never operates the daily pet.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from codex_pet.topmost import POLL_SECONDS, WindowsTopmostGuard, WindowsWindowOrder, repair_reason


def fixture_window(parent=None):
    window = QWidget(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    window.setAttribute(Qt.WA_ShowWithoutActivating)
    window.setWindowTitle("CodexTools window-order fixture")
    window.setGeometry(32, 32, 80, 80)
    window.show()
    return window


def test_real_native_order_repairs_repeated_cover_and_band_loss_without_focus_or_geometry_changes():
    app = QApplication.instance() or QApplication([])
    if os.name != "nt" or app.platformName() != "windows":
        pytest.skip("Opt in with QT_QPA_PLATFORM=windows; isolated native fixture only")
    from ctypes import wintypes as w
    api = WindowsWindowOrder()
    api.user.GetForegroundWindow.restype = w.HWND
    pet = fixture_window()
    panel = fixture_window(pet)
    panel.move(32, 115)
    details = fixture_window(panel)
    details.move(115, 32)
    surfaces = (pet, panel, details)
    child = None
    directory = Path(tempfile.mkdtemp(prefix="CodexToolsNativeFixture-"))
    sentinel = directory / "alive"
    sentinel.write_text("fixture", encoding="ascii")
    receipt = directory / "ready.json"
    try:
        app.processEvents()
        # A separate process supplies a real foreign topmost window above the pet.
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--cover", str(sentinel)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "windows"}, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 15
        while not receipt.exists() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert receipt.exists(), "Native fixture did not start"
        assert json.loads(receipt.read_text(encoding="ascii"))["fixture_pid"] != os.getpid()
        ordered = api.snapshot()
        handles = [int(surface.winId()) for surface in surfaces]
        assert repair_reason(handles, ordered, os.getpid()) == "occluded"
        assert all(item.topmost for item in ordered if item.handle in handles)
        original = {item.handle: item.rect for item in ordered if item.handle in handles}
        now = [0.0]
        guard = WindowsTopmostGuard(surfaces, backend=api, clock=lambda: now[0])
        foreground = api.user.GetForegroundWindow()
        assert guard.refresh()
        assert api.user.GetForegroundWindow() == foreground
        assert repair_reason(handles, api.snapshot(), os.getpid()) is None
        # Losing the native topmost band after creation must also recover repeatedly.
        for _ in range(20):
            assert api.set_band(handles[0], False)
            assert repair_reason(handles, api.snapshot(), os.getpid()) == "lost-topmost"
            now[0] += POLL_SECONDS + 0.01
            foreground = api.user.GetForegroundWindow()
            assert guard.refresh()
            assert api.user.GetForegroundWindow() == foreground
            assert repair_reason(handles, api.snapshot(), os.getpid()) is None
        assert guard.repairs == 21
        final = {item.handle: item.rect for item in api.snapshot() if item.handle in handles}
        assert final == original
        for surface in surfaces:
            surface.hide()
        now[0] += POLL_SECONDS + 0.01
        assert not guard.refresh()
        for surface in surfaces:
            surface.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            surface.show()
        app.processEvents()
        now[0] += POLL_SECONDS + 0.01
        assert not guard.refresh()
    finally:
        for surface in reversed(surfaces):
            surface.close()
        sentinel.unlink(missing_ok=True)
        if child is not None:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=5)
        app.processEvents()


if __name__ == "__main__" and "--cover" in sys.argv:
    app = QApplication([])
    cover = fixture_window()
    app.processEvents()
    sentinel = Path(sys.argv[sys.argv.index("--cover") + 1])
    receipt = sentinel.with_name("ready.json")
    pending = receipt.with_suffix(".tmp")
    pending.write_text(json.dumps({"fixture_pid": os.getpid()}), encoding="ascii")
    pending.replace(receipt)  # publish only complete JSON to the polling parent
    timer = QTimer(interval=100)
    timer.timeout.connect(lambda: None if sentinel.exists() else app.quit())
    timer.start()
    app.exec()
