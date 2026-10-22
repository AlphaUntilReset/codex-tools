"""Window order, focus and user-intent regressions using independent fixtures."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from codex_pet.topmost import (POLL_SECONDS, SWP_PRESERVE, WindowState,
    WindowsTopmostGuard, repair_reason)
from codex_pet.visibility import PetVisibility

app = QApplication.instance() or QApplication([])
PID = 10
PET = WindowState(1, PID, (100, 100, 200, 300))
PANEL = WindowState(2, PID, (100, 300, 200, 330))


@pytest.mark.parametrize("cover,reason", [
    (WindowState(9, 99, (0, 0, 400, 400)), "occluded"),
    (WindowState(9, 99, (0, 0, 400, 400), topmost=False), "occluded"),
    (WindowState(9, 99, (210, 0, 400, 400)), None),
    (WindowState(9, 99, (0, 0, 400, 400), visible=False), None),
    (WindowState(9, 99, (0, 0, 400, 400), minimized=True), None),
    (WindowState(9, 99, (0, 0, 400, 400), cloaked=True), None),
    (WindowState(9, PID, (0, 0, 400, 400)), None),
    (WindowState(9, 99, (0, 0, 400, 400), kind="#32768"), None),
    (WindowState(9, 99, (0, 0, 400, 400), kind="Shell_TrayWnd"), None),
])
def test_real_order_matters_more_than_topmost_bit(cover, reason):
    assert repair_reason([1, 2], [cover, PANEL, PET], PID) == reason


def test_windows_below_pet_and_invisible_pet_do_not_trigger_repair():
    cover = WindowState(9, 99, (0, 0, 400, 400))
    assert repair_reason([1], [PET, cover], PID) is None
    assert repair_reason([1], [cover, WindowState(1, PID, PET.rect, visible=False)], PID) is None


def test_native_topmost_bit_loss_is_repaired_without_an_overlapping_window():
    assert repair_reason([1], [WindowState(1, PID, PET.rect, topmost=False)], PID) == "lost-topmost"


class Surface:
    def __init__(self, handle, *, visible=True, topmost=True):
        self.handle, self.visible, self.topmost = handle, visible, topmost
    def isVisible(self):
        return self.visible
    def winId(self):
        return self.handle
    def windowFlags(self):
        return Qt.WindowStaysOnTopHint if self.topmost else Qt.Widget


class Backend:
    def __init__(self, ordered):
        self.ordered, self.calls, self.fail, self.queries = ordered, [], False, 0
    def snapshot(self):
        self.queries += 1
        return self.ordered
    def set_band(self, handle, band):
        self.calls.append((handle, band))
        return not self.fail


def guard_fixture():
    cover = WindowState(9, 99, (0, 0, 400, 400))
    backend = Backend([cover, PANEL, PET])
    surfaces = [Surface(1), Surface(2), Surface(3, visible=False)]
    now = [0.0]
    guard = WindowsTopmostGuard(surfaces, backend=backend, clock=lambda: now[0], pid=PID)
    return guard, backend, surfaces, now


def test_owner_group_repaired_together_and_never_moves_foreign_or_hidden_windows():
    guard, backend, surfaces, now = guard_fixture()
    assert guard.refresh()
    assert backend.calls == [(1, False), (2, False), (1, True), (2, True)]
    assert SWP_PRESERVE & 0x10  # NOACTIVATE: never take the keyboard focus.
    assert SWP_PRESERVE & 0x3 == 0x3  # Preserve size and position.
    assert not SWP_PRESERVE & 0x40  # No SHOWWINDOW: never revive hidden windows.


def test_healthy_order_does_not_write_and_polling_is_bounded():
    guard, backend, surfaces, now = guard_fixture()
    backend.ordered = [PANEL, PET]
    assert not guard.refresh()
    for _ in range(10):
        guard.refresh()
    assert backend.queries == 1 and not backend.calls
    now[0] += POLL_SECONDS
    guard.refresh()
    assert backend.queries == 2


def test_user_disabling_topmost_and_hiding_all_surfaces_is_respected():
    guard, backend, surfaces, now = guard_fixture()
    for surface in surfaces:
        surface.topmost = False
    assert not guard.refresh() and not backend.queries
    for surface in surfaces:
        surface.topmost, surface.visible = True, False
    now[0] += POLL_SECONDS
    assert not guard.refresh() and not backend.queries


def test_tray_menu_and_modal_dialog_are_not_covered(monkeypatch):
    guard, backend, surfaces, now = guard_fixture()
    monkeypatch.setattr(QApplication, "activePopupWidget", lambda: object())
    assert not guard.refresh() and not backend.queries
    monkeypatch.setattr(QApplication, "activePopupWidget", lambda: None)
    monkeypatch.setattr(QApplication, "activeModalWidget", lambda: object())
    now[0] += POLL_SECONDS
    assert not guard.refresh() and not backend.queries


def test_handle_reused_by_another_process_cannot_be_repaired():
    guard, backend, surfaces, now = guard_fixture()
    backend.ordered = [WindowState(1, 999, PET.rect, topmost=False)]
    assert not guard.refresh() and not backend.calls


def test_failed_repair_still_repromotes_group_then_retries_later():
    guard, backend, surfaces, now = guard_fixture()
    backend.fail = True
    assert not guard.refresh()
    assert backend.calls[-2:] == [(1, True), (2, True)]
    backend.fail = False
    now[0] += POLL_SECONDS
    assert guard.refresh()


def test_repeated_native_order_loss_self_heals_without_a_restart():
    guard, backend, surfaces, now = guard_fixture()
    for _ in range(20):
        assert guard.refresh()
        now[0] += POLL_SECONDS + 0.001
    assert guard.repairs == 20
    assert all(handle in (1, 2) for handle, band in backend.calls)


def test_fullscreen_unknown_probe_and_manual_hide_suspend_native_repairs():
    pet = QWidget()
    result, calls = [False], []
    class Guard:
        def refresh(self):
            calls.append(True)
    visibility = PetVisibility(pet, lambda hwnd: result[0], topmost=Guard())
    try:
        visibility.start(True)
        visibility.refresh()
        assert calls
        calls.clear()
        result[0] = True
        visibility.refresh()
        assert not pet.isVisible() and not calls
        result[0] = None
        visibility.refresh()
        assert not pet.isVisible() and not calls
        result[0] = False
        visibility.refresh()
        assert not pet.isVisible() and not calls
        visibility.refresh()
        assert pet.isVisible() and calls
        visibility.request_hide()
        calls.clear()
        visibility.refresh()
        assert not pet.isVisible() and not calls
    finally:
        visibility.stop()
        pet.close()


def test_offscreen_backend_never_calls_native_windows():
    if app.platformName() != "offscreen":
        pytest.skip("Requires the normal isolated offscreen test platform")
    assert WindowsTopmostGuard([Surface(1)]).backend is None
