"""随官方 Codex 进程显隐：边沿驱动、防抖确认、手动操作不被覆盖。"""
import os
import sys
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from codex_pet.visibility import CodexProcessFollower, PetVisibility

app = QApplication.instance() or QApplication([])


class FakeWindow:
    def __init__(self):
        self.visible = True
        self.winId = lambda: 0
    def isVisible(self):
        return self.visible
    def setVisible(self, show):
        self.visible = show


class FakeVisibility(QObject):
    def __init__(self):
        super().__init__()
        self.window = FakeWindow()
        self.requested_visible = True
    def request_show(self):
        self.requested_visible = True
        self.window.setVisible(True)
    def request_hide(self):
        self.requested_visible = False
        self.window.setVisible(False)


def make(probe_results):
    seq = list(probe_results)
    vis = FakeVisibility()
    follower = CodexProcessFollower(vis, probe=lambda: seq.pop(0) if seq else seq.append(None) or False)
    return vis, follower


def test_start_aligns_to_current_state():
    vis, f = make([False])
    f.start()
    assert not vis.window.visible          # 开启时官方不在 → 立即隐藏
    vis2, f2 = make([True])
    f2.start()
    assert vis2.window.visible
    f.stop(); f2.stop()


def test_disappear_needs_two_confirmations_then_show_recovers():
    vis, f = make([True])                  # start：官方在 → 显示
    f.start()
    f._probe = lambda: False
    f._tick()
    assert vis.window.visible              # 第 1 次探测不到：仍显示（防抖）
    f._tick()
    assert not vis.window.visible          # 第 2 次确认：隐藏
    f._probe = lambda: True
    f._tick()
    assert vis.window.visible              # 官方回来：现身
    f.stop()


def test_persistent_state_does_not_override_manual_toggle():
    vis, f = make([True])
    f.start()
    vis.request_hide()                     # 用户手动隐藏（官方仍在）
    f._tick()                              # 持续"在"：不动作
    f._tick()
    assert not vis.window.visible          # 手动隐藏保持
    f.stop()


def test_transient_single_miss_does_not_hide():
    vis, f = make([True])
    f.start()
    probes = iter([False, True, False, False, True])
    f._probe = lambda: next(probes, True)
    f._tick(); assert vis.window.visible   # 抖动 1 次不隐藏
    f._tick(); assert vis.window.visible   # 回来
    f._tick(); f._tick()                   # 连续 2 次才隐藏
    assert not vis.window.visible
    f.stop()
