"""Fullscreen geometry and user intent regressions, independent of the current desktop."""
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'src'))

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from codex_pet.bridge import BridgeState, Connection
from codex_pet.task_panel import TaskPanel
from codex_pet.visibility import PetVisibility, covers_monitor

app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('client,monitor,captioned,expected', [
    ((0, 0, 2560, 1600), (0, 0, 2560, 1600), False, True),
    ((0, 0, 2560, 1528), (0, 0, 2560, 1600), False, False),
    ((0, 0, 2560, 1600), (0, 0, 2560, 1600), True, False),
    ((-1920, 0, 0, 1080), (-1920, 0, 0, 1080), False, True),
    ((0, 0, 2560, 1600), (-1920, 0, 0, 1080), False, False),
    ((1, 1, 2559, 1599), (0, 0, 2560, 1600), False, True),
])
def test_fullscreen_geometry(client, monitor, captioned, expected):
    assert covers_monitor(client, monitor, captioned) is expected


@pytest.fixture
def visibility():
    pet = QWidget()
    pet.resize(251, 272)
    result = [False]
    guard = PetVisibility(pet, lambda hwnd: result[0])
    yield pet, guard, result
    guard.stop()
    pet.hide()
    pet.deleteLater()
    app.processEvents()


def test_fullscreen_hides_all_surfaces_and_restores_expansion(visibility):
    pet, guard, result = visibility
    panel = TaskPanel(pet)
    panel.set_state(BridgeState('demo', 'demo', Connection.CONNECTED))
    guard.start(True)
    app.processEvents()
    panel.set_collapsed(False)
    assert pet.isVisible() and panel.isVisible() and panel.details.isVisible()
    result[0] = True
    guard.refresh()
    assert not any(w.isVisible() for w in (pet, panel, panel.details))
    panel.set_state(BridgeState('demo', 'demo', Connection.DISCONNECTED))
    assert not panel.isVisible()
    result[0] = False
    guard.refresh()
    assert not pet.isVisible()  # Debounce the exit; a transient focus change is not enough.
    guard.refresh()
    app.processEvents()
    assert pet.isVisible() and panel.isVisible() and panel.details.isVisible()


def test_starting_during_fullscreen_and_show_request_cannot_flash(visibility):
    pet, guard, result = visibility
    result[0] = True
    guard.start(True)
    guard.request_show()
    assert guard.requested_visible and not pet.isVisible()
    result[0] = None
    guard.refresh()
    assert not pet.isVisible()


def test_manual_hide_survives_fullscreen_exit_and_later_show_works(visibility):
    pet, guard, result = visibility
    guard.start(True)
    result[0] = True
    guard.refresh()
    guard.toggle()  # User hides via the tray/hotkey while automatically hidden.
    result[0] = False
    guard.refresh()
    guard.refresh()
    assert not pet.isVisible() and not guard.requested_visible
    guard.request_show()
    assert pet.isVisible()


def test_disabled_start_is_not_undone_by_fullscreen_exit(visibility):
    pet, guard, result = visibility
    guard.start(False)
    for result[0] in [True, False, False]:
        guard.refresh()
    assert not pet.isVisible()
