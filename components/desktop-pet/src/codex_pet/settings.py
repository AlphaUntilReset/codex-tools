"""版本化设置：原子写入、损坏恢复默认（见施工方案 6）。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

SCHEMA_VERSION = 1

# 画布逻辑高度（DIP）默认与范围；宽高比锁定 768/832
DEFAULT_CANVAS_HEIGHT_DIP = 320
MIN_CANVAS_HEIGHT_DIP = 160
MAX_CANVAS_HEIGHT_DIP = 640
ASPECT_W, ASPECT_H = 768, 832


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    canvas_height_dip: int = DEFAULT_CANVAS_HEIGHT_DIP
    always_on_top: bool = True
    animations_enabled: bool = True
    natural_behavior: bool = True
    life_actions: bool = True
    thinking_actions: bool = True
    reduced_motion: bool = False
    mouse_follow: bool = False
    click_through: bool = False
    hover_passthrough: bool = True
    bridge_mode: str = "disabled"  # disabled | demo
    show_on_start: bool = True
    show_task_panel: bool = True
    task_panel_collapsed: bool = True
    attention_only: bool = False
    global_hotkey: bool = True
    autostart: bool = False  # 开机自启（随官方 Codex 一起拉起）；键值见 autostart.py
    follow_codex_visibility: bool = False  # 随官方 Codex 进程显隐（显隐跟随）
    # 位置：以屏幕名 + 逻辑坐标保存，未指定时居中主屏下方
    screen_name: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None

    def logical_size(self) -> tuple[int, int]:
        h = max(MIN_CANVAS_HEIGHT_DIP, min(MAX_CANVAS_HEIGHT_DIP, int(self.canvas_height_dip)))
        w = round(h * ASPECT_W / ASPECT_H)
        return w, h

    def clamp_canvas_height(self, value: int) -> int:
        return max(MIN_CANVAS_HEIGHT_DIP, min(MAX_CANVAS_HEIGHT_DIP, int(value)))


VALID_BRIDGE_MODES = ("disabled", "demo", "codex-local")


def _coerce(raw: dict) -> Settings:
    """按字段类型过滤与修正，未知字段忽略；明显坏值回默认。"""
    s = Settings()
    known = {f.name: f.type for f in fields(Settings)}
    for key, value in raw.items():
        if key not in known:
            continue
        try:
            if key == "schema_version":
                s.schema_version = int(value)
            elif key in ("canvas_height_dip",):
                s.canvas_height_dip = s.clamp_canvas_height(int(value))
            elif isinstance(getattr(s, key), bool):
                setattr(s, key, bool(value))
            elif key == "bridge_mode":
                v = str(value)
                s.bridge_mode = v if v in VALID_BRIDGE_MODES else "disabled"
            elif key == "screen_name":
                s.screen_name = str(value) if value else None
            elif key in ("pos_x", "pos_y"):
                setattr(s, key, float(value) if value is not None else None)
        except (TypeError, ValueError):
            continue
    return s


def settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_settings(data_dir: Path) -> tuple[Settings, str | None]:
    """读取设置；损坏时保留问题文件并回默认。返回 (settings, 备注)。"""
    path = settings_path(data_dir)
    if not path.exists():
        return Settings(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("settings 根不是对象")
        return _coerce(raw), None
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        backup = path.with_suffix(f".corrupt-{os.getpid()}.json")
        try:
            path.replace(backup)
        except OSError:
            backup = path  # 重命名失败时只读跳过
        return Settings(), f"配置损坏已隔离: {backup.name} ({exc})"


def save_settings(settings: Settings, data_dir: Path) -> None:
    """临时文件 + 原子替换写入。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(settings), ensure_ascii=False, indent=1)
    path = settings_path(data_dir)
    fd, tmp_name = tempfile.mkstemp(dir=str(data_dir), prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
