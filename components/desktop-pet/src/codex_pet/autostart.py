"""开机自启：用户级 HKCU Run 键（无需管理员、可逆、不装服务）。

写入 `CodexToolsPet` = `"<exe>" --launch-codex`：开机登录后宠物自行启动，
并顺手拉起官方 Codex 桌面版（与桌面 Codex.lnk 行为一致）。
源码模式写入 pythonw + run_pet.py；打包模式写入 exe 全路径。
"""

from __future__ import annotations

import ntpath
import re
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CodexToolsPet"
PREFERENCE_KEY = r"Software\CodexToolsPet"
PREFERENCE_NAME = "LoginEnabled"


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --launch-codex'
    # 源码模式：pythonw.exe 与 python.exe 并排在安装根目录（不在 Scripts），
    # 用它避免开机自启时弹出控制台窗口。
    root = Path(__file__).resolve().parent.parent.parent
    entry = root / "run_pet.py"
    pythonw = Path(sys.base_prefix) / "pythonw.exe"
    python = str(pythonw if pythonw.exists() else Path(sys.executable))
    return f'"{python}" "{entry}" --launch-codex'


def _read_value(reg, path: str, name: str):
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_QUERY_VALUE) as key:
            return reg.QueryValueEx(key, name)
    except FileNotFoundError:
        return None


def _write_value(reg, path: str, name: str, value) -> None:
    if value is None:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, name)
        except FileNotFoundError:
            pass
    else:
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, path, 0, reg.KEY_SET_VALUE) as key:
            reg.SetValueEx(key, name, 0, value[1], value[0])


def _preference(reg, value) -> bool | None:
    # Missing/invalid values preserve legacy integrated-login behaviour.
    if value is not None and value[1] == reg.REG_DWORD and value[0] in (0, 1):
        return bool(value[0])
    return None


def _managed_command(value, reg) -> bool:
    if value is None:
        return True
    command, kind = value
    if kind not in (reg.REG_SZ, reg.REG_EXPAND_SZ) or not isinstance(command, str):
        return False
    # Only our generated commands are owned. Preserve user wrappers/extra flags.
    match = re.fullmatch(r'"([^"\r\n]+)"(?:\s+"([^"\r\n]+)")?\s+--launch-codex', command.strip())
    if not match:
        return False
    executable, script = match.groups()
    if script is None:
        return ntpath.basename(executable).casefold() == "codextoolspet.exe"
    return (ntpath.basename(executable).casefold() in ("python.exe", "pythonw.exe")
            and ntpath.basename(script).casefold() == "run_pet.py")


def set_autostart(enabled: bool, _winreg=None, *, explicit: bool = True) -> bool:
    """更新自启及组合入口共享的用户意图；启动维修不创建入口或重写意图。"""
    if sys.platform != "win32":
        return False
    import winreg  # noqa: WPS433（仅 Windows 导入）
    reg = _winreg or winreg
    try:
        original_run = _read_value(reg, RUN_KEY, VALUE_NAME)
        original_preference = _read_value(reg, PREFERENCE_KEY, PREFERENCE_NAME)
    except OSError:
        return False
    if not explicit and (not enabled or original_run is None
                         or _preference(reg, original_preference) is False):
        return True
    if not _managed_command(original_run, reg):
        return not explicit
    try:
        _write_value(reg, RUN_KEY, VALUE_NAME, (_command(), reg.REG_SZ) if enabled else None)
        if explicit:
            _write_value(reg, PREFERENCE_KEY, PREFERENCE_NAME, (int(enabled), reg.REG_DWORD))
        return True
    except OSError:
        # Restore both values on partial failure; never report a successful toggle.
        restore = [(RUN_KEY, VALUE_NAME, original_run)]
        if explicit:
            restore.append((PREFERENCE_KEY, PREFERENCE_NAME, original_preference))
        for path, name, value in restore:
            try:
                _write_value(reg, path, name, value)
            except OSError:
                pass
        return False


def autostart_enabled(_winreg=None) -> bool:
    """读取自启键；显式关闭意图优先，未知意图兼容历史安装。"""
    if sys.platform != "win32":
        return False
    import winreg
    reg = _winreg or winreg
    try:
        if _preference(reg, _read_value(reg, PREFERENCE_KEY, PREFERENCE_NAME)) is False:
            return False
        value = _read_value(reg, RUN_KEY, VALUE_NAME)
        return bool(value and value[0])
    except (FileNotFoundError, OSError):
        return False
