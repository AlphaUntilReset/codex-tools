"""托盘设置开关回归（0.4.4）：_patch 必须发副本而非原地改共享 Settings。

0.4.3 P0：_patch 原地 setattr 共享对象再原对象 emit，app.apply_settings 的
差值守卫（settings.autostart != self.settings.autostart）变成对象与自身比较，
导致托盘取消"开机自启/随 Codex 显隐"不生效。此处锁定失效条件不再复发。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from codex_pet.settings import Settings  # noqa: E402
from codex_pet.tray import TrayController  # noqa: E402

app = QApplication.instance() or QApplication([])


@pytest.fixture()
def harness():
    base = Settings(autostart=True, follow_codex_visibility=True, mouse_follow=False)
    tray = TrayController(QIcon(), base)
    emitted: list[Settings] = []
    tray.apply_settings.connect(emitted.append)
    yield base, tray, emitted
    tray.deleteLater()


def test_patch_emits_independent_copy(harness):
    base, tray, emitted = harness
    tray._patch(autostart=False)
    assert len(emitted) == 1
    s = emitted[0]
    assert s is not base, "必须发副本：app.apply_settings 依赖新旧对象差值分发"
    assert s.autostart is False
    assert base.autostart is True, "共享对象不得被原地修改"


def test_patch_only_changes_requested_keys(harness):
    base, tray, emitted = harness
    tray._patch(canvas_height_dip=480)
    s = emitted[0]
    assert s.canvas_height_dip == 480
    assert s.autostart == base.autostart
    assert s.follow_codex_visibility == base.follow_codex_visibility


def test_diff_guard_fires_for_guarded_branches(harness):
    """app.apply_settings 三个差值守卫分支（bridge_mode/autostart/follow）的触发条件。"""
    base, tray, emitted = harness
    tray._patch(autostart=False)
    assert emitted[0].autostart != base.autostart
    tray._patch(follow_codex_visibility=False)
    assert emitted[1].follow_codex_visibility != base.follow_codex_visibility


def test_sync_from_settings_does_not_emit(harness):
    """sync 同步菜单勾选不得触发 apply_settings 回环（_follow_action 原缺 blocker）。"""
    base, tray, emitted = harness
    changed = Settings(**{k: getattr(base, k) for k in base.__dict__})
    changed.mouse_follow = True
    changed.autostart = False
    tray.sync_from_settings(changed)
    assert emitted == []
    assert tray._settings is changed


def test_tray_icon_safely_degrades_on_missing_frame(tmp_path):
    """0.4.4：托盘图标取帧失败降级为空白图标，不抛异常（启动路径防崩溃）。"""
    from PySide6.QtGui import QIcon

    from codex_pet.app import tray_icon_safely
    from codex_pet.renderer import FrameRenderer

    class BrokenRenderer:
        def full_frame(self, row, col):
            raise FileNotFoundError("帧缓存缺失或损坏: (模拟)")

    fallback = tray_icon_safely(BrokenRenderer())
    assert isinstance(fallback, QIcon)

    cache = ROOT / "build" / "frames"
    if not cache.exists():
        pytest.skip("先运行 tools/prepare_assets.py")
    good = tray_icon_safely(FrameRenderer(cache))
    assert not good.isNull()
