"""开机自启：命令行构造与注册表读写（注入假 winreg，不动真实 HKCU）。"""
import os
import sys
from pathlib import Path
import pytest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from codex_pet.autostart import (
    RUN_KEY, VALUE_NAME, PREFERENCE_KEY, PREFERENCE_NAME,
    _command, autostart_enabled, set_autostart,
)


class FakeWinreg:
    HKEY_CURRENT_USER = 'HKCU'
    REG_SZ = 1
    REG_EXPAND_SZ = 2
    REG_BINARY = 3
    REG_DWORD = 4
    KEY_SET_VALUE = 2
    KEY_QUERY_VALUE = 4
    FileNotFoundError = FileNotFoundError

    def __init__(self):
        self.values = {}
        self.keys = {RUN_KEY}
        self.fail_once = None
        self.writes = []

    class _Key:
        def __init__(self, path):
            self.path = path
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, _res, access):
        assert root == self.HKEY_CURRENT_USER
        self._fail(path, 'read' if access == self.KEY_QUERY_VALUE else 'open-write')
        if path not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(path)

    def CreateKeyEx(self, root, path, _res, access):
        assert (root, access) == (self.HKEY_CURRENT_USER, self.KEY_SET_VALUE)
        self._fail(path, 'create')
        self.keys.add(path)
        return self._Key(path)

    def _fail(self, path, operation):
        if self.fail_once == (path, operation):
            self.fail_once = None
            raise PermissionError('injected registry failure')

    def SetValueEx(self, key, name, _r, kind, value):
        self._fail(key.path, 'set')
        self.values[key.path, name] = (value, kind)
        self.writes.append((key.path, name))

    def DeleteValue(self, key, name):
        self._fail(key.path, 'delete')
        if (key.path, name) not in self.values:
            raise FileNotFoundError(name)
        del self.values[key.path, name]
        self.writes.append((key.path, name))

    def QueryValueEx(self, key, name):
        if (key.path, name) not in self.values:
            raise FileNotFoundError(name)
        return self.values[key.path, name]

    def seed(self, path, name, value, kind=REG_SZ):
        self.keys.add(path)
        self.values[path, name] = (value, kind)


def test_command_contains_launch_codex_and_quoted_path():
    cmd = _command()
    assert cmd.endswith('--launch-codex')
    assert cmd.startswith('"') and '" "' in cmd or cmd.count('"') >= 2


def test_set_and_query_roundtrip():
    reg = FakeWinreg()
    assert set_autostart(True, _winreg=reg) is True
    assert reg.values[RUN_KEY, VALUE_NAME][0].endswith('--launch-codex')
    assert reg.values[PREFERENCE_KEY, PREFERENCE_NAME] == (1, reg.REG_DWORD)
    assert autostart_enabled(_winreg=reg) is True
    assert set_autostart(False, _winreg=reg) is True
    assert (RUN_KEY, VALUE_NAME) not in reg.values
    assert reg.values[PREFERENCE_KEY, PREFERENCE_NAME] == (0, reg.REG_DWORD)
    assert autostart_enabled(_winreg=reg) is False


def test_disable_when_absent_is_success():
    reg = FakeWinreg()
    assert set_autostart(False, _winreg=reg) is True
    # The explicit intent also suppresses an existing wallpaper integration.
    assert reg.values[PREFERENCE_KEY, PREFERENCE_NAME] == (0, reg.REG_DWORD)


def test_reenable_preserves_startup_approval_and_unrelated_entries():
    reg = FakeWinreg()
    approval = RUN_KEY.replace(r'CurrentVersion\Run', r'CurrentVersion\Explorer\StartupApproved\Run')
    reg.seed(approval, VALUE_NAME, b'\x03\0\0\0keep', reg.REG_BINARY)
    reg.seed(RUN_KEY, 'CodexSunFaded', 'existing wallpaper login')
    original = dict(reg.values)
    assert set_autostart(False, _winreg=reg)
    assert set_autostart(True, _winreg=reg)
    for key, value in original.items():
        assert reg.values[key] == value
    assert set(reg.writes) <= {(RUN_KEY, VALUE_NAME), (PREFERENCE_KEY, PREFERENCE_NAME)}


def test_startup_repair_only_updates_an_existing_owned_command():
    reg = FakeWinreg()
    assert set_autostart(True, _winreg=reg, explicit=False)
    assert not reg.values and not reg.writes
    reg.seed(RUN_KEY, VALUE_NAME, '"D:\\previous install\\CodexToolsPet.exe" --launch-codex')
    assert set_autostart(True, _winreg=reg, explicit=False)
    assert reg.values[RUN_KEY, VALUE_NAME] == (_command(), reg.REG_SZ)
    assert (PREFERENCE_KEY, PREFERENCE_NAME) not in reg.values


def test_startup_repair_cannot_override_explicit_disable_with_stale_settings():
    reg = FakeWinreg()
    reg.seed(RUN_KEY, VALUE_NAME, '"D:\\old\\CodexToolsPet.exe" --launch-codex')
    reg.seed(PREFERENCE_KEY, PREFERENCE_NAME, 0, reg.REG_DWORD)
    before = dict(reg.values)
    assert set_autostart(True, _winreg=reg, explicit=False)
    assert reg.values == before and not reg.writes
    assert not autostart_enabled(_winreg=reg)


@pytest.mark.parametrize('value,kind', [('0', FakeWinreg.REG_SZ), (2, FakeWinreg.REG_DWORD), (b'\0', FakeWinreg.REG_BINARY)])
def test_unknown_preference_preserves_legacy_semantics_and_bytes(value, kind):
    reg = FakeWinreg()
    reg.seed(RUN_KEY, VALUE_NAME, '"D:\\old\\CodexToolsPet.exe" --launch-codex')
    reg.seed(PREFERENCE_KEY, PREFERENCE_NAME, value, kind)
    assert set_autostart(True, _winreg=reg, explicit=False)
    assert autostart_enabled(_winreg=reg)
    assert reg.values[PREFERENCE_KEY, PREFERENCE_NAME] == (value, kind)


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('failure', ['read', 'create', 'set'])
def test_preference_failure_restores_original_run_and_intent(enabled, failure):
    reg = FakeWinreg()
    reg.seed(RUN_KEY, VALUE_NAME, '"D:\\old\\CodexToolsPet.exe" --launch-codex')
    reg.seed(PREFERENCE_KEY, PREFERENCE_NAME, 'legacy unknown', reg.REG_SZ)
    before = dict(reg.values)
    reg.fail_once = (PREFERENCE_KEY, failure)
    assert not set_autostart(enabled, _winreg=reg)
    assert reg.values == before


def test_new_preference_write_failure_does_not_leave_partial_enable():
    reg = FakeWinreg()
    reg.fail_once = (PREFERENCE_KEY, 'set')
    assert not set_autostart(True, _winreg=reg)
    assert not reg.values


def test_startup_repair_failure_does_not_write_shared_preference():
    reg = FakeWinreg()
    reg.seed(RUN_KEY, VALUE_NAME, '"D:\\old\\CodexToolsPet.exe" --launch-codex')
    reg.seed(PREFERENCE_KEY, PREFERENCE_NAME, 1, reg.REG_DWORD)
    before = dict(reg.values)
    reg.fail_once = (RUN_KEY, 'set')
    assert not set_autostart(True, _winreg=reg, explicit=False)
    assert reg.values == before
    assert all(path == RUN_KEY for path, _ in reg.writes)


@pytest.mark.parametrize('enabled', [False, True])
def test_run_failure_preserves_existing_preference(enabled):
    reg = FakeWinreg()
    reg.seed(RUN_KEY, VALUE_NAME, '"D:\\old\\CodexToolsPet.exe" --launch-codex')
    reg.seed(PREFERENCE_KEY, PREFERENCE_NAME, 1, reg.REG_DWORD)
    before = dict(reg.values)
    reg.fail_once = (RUN_KEY, 'set' if enabled else 'delete')
    assert not set_autostart(enabled, _winreg=reg)
    assert reg.values == before


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('command', ['"D:\\user-wrapper.cmd" --launch-codex', '"D:\\CodexToolsPet.exe" --launch-codex --custom'])
def test_user_managed_command_is_never_replaced_or_deleted(enabled, command):
    reg = FakeWinreg()
    reg.seed(RUN_KEY, VALUE_NAME, command)
    before = dict(reg.values)
    assert not set_autostart(enabled, _winreg=reg)
    assert set_autostart(True, _winreg=reg, explicit=False)
    assert reg.values == before and not reg.writes


def test_settings_roundtrip_autostart():
    from codex_pet.settings import Settings
    s = Settings(autostart=True)
    assert s.autostart is True
    s2 = Settings()
    assert s2.autostart is False  # 产品默认关闭（AGENTS 约定）
