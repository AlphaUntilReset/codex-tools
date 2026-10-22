"""Joint sprite/capsule hover; only isolated Qt windows, no physical input."""
import ctypes
from dataclasses import replace
import os
from pathlib import Path
import sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from codex_pet.bridge import BridgeState, Connection
from codex_pet.task_panel import TaskPanel
from test_hover_passthrough import APP, pet, through


@pytest.fixture
def pair(pet):
    window,cursor,modifiers=pet
    panel=TaskPanel(window)
    panel.apply_settings(window._settings)
    panel.set_state(BridgeState('demo','demo',Connection.CONNECTED))
    APP.processEvents()
    yield window,panel,cursor,modifiers
    panel.close()
    panel.deleteLater()
    APP.processEvents()


def at(window,panel,cursor,which):
    target=panel if which=='pill' else window
    cursor[0]=target.mapToGlobal(target.rect().center())
    window._poll_hover()


@pytest.mark.parametrize('which',['pill','body'])
def test_both_surfaces_fade_for_either_hover_and_restore(pair,which):
    window,panel,cursor,_=pair
    at(window,panel,cursor,which)
    assert window._hover_active
    assert getattr(panel,'_hover_active',False)
    assert through(window)
    cursor[0]=QPoint(-20000,-20000)
    QTest.qWait(90)
    assert not window._hover_active and not panel._hover_active


def test_ctrl_click_opens_details_and_holds_interaction_until_release(pair):
    window,panel,cursor,modifiers=pair
    at(window,panel,cursor,'pill')
    assert window._hover_active
    modifiers[0]=Qt.ControlModifier
    QTest.mousePress(panel.collapse,Qt.LeftButton,Qt.ControlModifier,panel.collapse.rect().center())
    assert not window._hover_active and not panel._hover_active
    modifiers[0]=Qt.NoModifier
    window._poll_hover()
    assert not window._hover_active  # completing a capsule press never turns transparent mid-click
    QTest.mouseRelease(panel.collapse,Qt.LeftButton,Qt.NoModifier,panel.collapse.rect().center())
    APP.processEvents()
    assert not panel.collapse.isChecked() and panel.details.isVisible()
    cursor[0]=panel.details.mapToGlobal(panel.details.rect().center())
    window._poll_hover()
    assert not window._hover_active and not panel._hover_active


def test_rounded_margins_hidden_pill_and_disabled_feature_do_not_trigger(pair):
    window,panel,cursor,_=pair
    cursor[0]=panel.mapToGlobal(QPoint(0,0))
    window._poll_hover()
    assert not window._hover_active
    at(window,panel,cursor,'pill')
    assert window._hover_active
    panel.apply_settings(replace(window._settings,show_task_panel=False))
    window._poll_hover()
    assert not panel.isVisible() and not window._hover_active
    panel.apply_settings(window._settings)
    window.apply_settings(replace(window._settings,hover_passthrough=False))
    at(window,panel,cursor,'pill')
    assert not window._hover_active and not panel._hover_active


def test_capsule_pixels_fade_without_window_opacity_or_geometry_changes(pair):
    window,panel,cursor,_=pair
    geometry=panel.geometry()
    def render():
        image=QImage(panel.size(),QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        panel.render(image)
        return image
    normal=render()
    at(window,panel,cursor,'pill')
    faded=render()
    point=QPoint(panel.width()//2,5)
    assert normal.pixelColor(point).alpha()>200
    assert 1 <= faded.pixelColor(point).alpha() <= 70
    assert panel.windowOpacity()==1.0 and window.windowOpacity()==1.0
    assert panel.geometry()==geometry


def test_manual_through_has_priority_and_static_motion_still_polls(pair):
    window,panel,cursor,modifiers=pair
    settings=replace(window._settings,click_through=True,reduced_motion=True)
    window.apply_settings(settings);panel.apply_settings(settings)
    at(window,panel,cursor,'pill')
    assert window._hover_active and panel._hover_active and window._hover_timer.isActive()
    modifiers[0]=Qt.ControlModifier;window._poll_hover()
    assert not window._hover_active and not panel._hover_active
    assert through(window)
    assert panel.windowHandle().flags() & Qt.WindowTransparentForInput


def test_hide_and_open_menu_restore_both_surfaces(pair,monkeypatch):
    window,panel,cursor,_=pair
    at(window,panel,cursor,'pill')
    monkeypatch.setattr(window,'_has_open_menu_or_dialog',lambda: True)
    window._poll_hover()
    assert not window._hover_active and not panel._hover_active
    monkeypatch.setattr(window,'_has_open_menu_or_dialog',lambda: False)
    window._poll_hover()
    assert window._hover_active and panel._hover_active
    window.hide();APP.processEvents()
    assert not window._hover_active and not panel._hover_active and not panel.isVisible()


def test_native_joint_hover_routes_both_hwnds_and_keeps_layered_style(pair):
    if os.name!='nt' or APP.platformName()!='windows':
        pytest.skip('Opt in with QT_QPA_PLATFORM=windows')
    from ctypes import wintypes as w
    from PySide6.QtCore import QAbstractNativeEventFilter
    window,panel,cursor,modifiers=pair
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.WindowFromPoint.argtypes=[w.POINT];user.WindowFromPoint.restype=w.HWND
    user.GetForegroundWindow.restype=w.HWND
    user.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
    handles=[int(window.winId()),int(panel.winId())]
    geometries=[window.geometry(),panel.geometry()]
    under=QWidget(None,Qt.Tool|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)
    under.setAttribute(Qt.WA_ShowWithoutActivating)
    under.setStyleSheet('background:white')
    under.setGeometry(window.geometry().united(panel.geometry()))
    under.show();window.raise_();panel.raise_();APP.processEvents()
    foreground=user.GetForegroundWindow()
    class Style(ctypes.Structure):
        _fields_=[('old',w.DWORD),('new',w.DWORD)]
    class Observe(QAbstractNativeEventFilter):
        def __init__(self):super().__init__();self.changes=[]
        def nativeEventFilter(self,kind,pointer):
            if kind==b'windows_generic_MSG':
                msg=w.MSG.from_address(int(pointer))
                if msg.hWnd in handles and msg.message==0x007D and ctypes.c_ssize_t(msg.wParam).value==-20:
                    s=Style.from_address(msg.lParam);self.changes.append((msg.hWnd,s.old,s.new))
            return False,0
    listener=Observe();APP.installNativeEventFilter(listener)
    def target(handle):
        rect=w.RECT();assert user.GetWindowRect(handle,ctypes.byref(rect))
        return user.WindowFromPoint(w.POINT((rect.left+rect.right)//2,(rect.top+rect.bottom)//2))
    try:
        assert [target(h) for h in handles]==handles
        for which in ['body','pill']*5:
            at(window,panel,cursor,which);QTest.qWait(10)
            assert window._hover_active and panel._hover_active
            assert all(target(h)==int(under.winId()) for h in handles)
            modifiers[0]=Qt.ControlModifier;window._poll_hover();QTest.qWait(10)
            assert [target(h) for h in handles]==handles
            modifiers[0]=Qt.NoModifier
        assert {h for h,_,_ in listener.changes}==set(handles)
        assert all(not((a^b)&~0x20) and a&0x80000 and b&0x80000 for _,a,b in listener.changes)
        assert [int(window.winId()),int(panel.winId())]==handles
        assert [window.geometry(),panel.geometry()]==geometries
        assert user.GetForegroundWindow()==foreground
    finally:
        APP.removeNativeEventFilter(listener);under.close();under.deleteLater()
