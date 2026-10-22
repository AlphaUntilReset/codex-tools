"""运行期路径解析：源码运行与 PyInstaller 冻结运行、中文/空格路径通用。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def app_base_dir() -> Path:
    """应用根目录（含 assets 的目录）。"""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def resource_base_dir() -> Path:
    """只读资源根目录；冻结时数据在 _internal（sys._MEIPASS）。"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(sys.executable).resolve().parent / "_internal"
        return base
    return app_base_dir()


def user_data_dir() -> Path:
    """用户数据目录 %LOCALAPPDATA%\\CodexToolsPet；可用环境变量覆盖（测试用）。"""
    override = os.environ.get("CODEXTOOLS_PET_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "CodexToolsPet"


def frame_cache_dir() -> Path:
    """运行帧缓存目录（衍生文件，不进安装目录）。"""
    return user_data_dir() / "cache" / "frames-v1"


def log_dir() -> Path:
    return user_data_dir() / "logs"
