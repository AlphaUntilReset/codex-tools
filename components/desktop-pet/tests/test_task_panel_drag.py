"""拖拽期间状态条 reposition 不得移动宠物本体（越界拖拽残影回归）。

reposition 的"脚部+胶囊超出工作区底部→整体上移宠物"在拖拽进行中会与
mouseMove 的跟随移动打架，窗口在两个位置间高频闪跳。断言：
- 非拖拽时：越界仍按原设计上移（含 move 事件内联触发，既有行为不变）；
- _drag_active 时：reposition（含 move 事件内联触发）不再移动宠物本体。
布景阶段用 _drag_active=True 屏蔽内联 reposition，保证取样位置是放置位置。
"""

import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from codex_pet.bridge import BridgeState, Connection  # noqa: E402
from codex_pet.settings import Settings  # noqa: E402
from codex_pet.task_panel import TaskPanel  # noqa: E402

app = QApplication.instance() or QApplication([])

PLACED_OFFSET = 150  # area.bottom()-150：脚部+胶囊明显越界（frameGeometry 略高于 resize）


def make_pair():
    pet = QWidget()
    pet.resize(251, 272)
    pet._drag_active = True  # 布景期间屏蔽 reposition 的内联上移
    panel = TaskPanel(pet)
    panel.apply_settings(Settings())  # show_task_panel=True → _enabled
    panel.set_state(BridgeState('demo', 'demo', Connection.CONNECTED))
    pet.show()
    app.processEvents()
    pet.move(200, pet.screen().availableGeometry().bottom() - PLACED_OFFSET)
    app.processEvents()
    return pet, panel


def teardown(pet, panel):
    pet.close()
    panel.close()
    pet.deleteLater()
    panel.deleteLater()
    app.processEvents()


def test_reposition_pulls_pet_up_when_idle():
    pet, panel = make_pair()
    try:
        area = pet.screen().availableGeometry()
        placed = pet.y()
        pet._drag_active = False
        panel.reposition()
        assert pet.y() < placed, "非拖拽时越界应上移（既有行为）"
        assert pet.y() >= area.top()
        # move 事件内联触发同样生效（真实链路：宠物每次移动都会进 reposition）。
        # move 的事件是同步送达的，所以先落位再关标志，然后 1px 轻推触发内联路径。
        pet._drag_active = True
        pet.move(200, area.bottom() - PLACED_OFFSET)
        placed = pet.y()
        pet._drag_active = False
        pet.move(200, placed + 1)
        app.processEvents()
        assert pet.y() < placed, "非拖拽 move 后应被 reposition 上移"
    finally:
        teardown(pet, panel)


def test_reposition_does_not_move_pet_while_dragging():
    pet, panel = make_pair()
    try:
        area = pet.screen().availableGeometry()
        placed = pet.y()
        panel.reposition()
        assert pet.y() == placed, "拖拽中 reposition 不得移动宠物本体（残影打架根因）"
        # move 事件内联触发也不得移动（真实拖拽链路：每个 Move 都进 reposition）
        target = area.bottom() - PLACED_OFFSET - 20
        pet.move(200, target)
        app.processEvents()
        assert pet.y() == target, "拖拽中 move 应保持拖拽放置的位置"
    finally:
        teardown(pet, panel)
