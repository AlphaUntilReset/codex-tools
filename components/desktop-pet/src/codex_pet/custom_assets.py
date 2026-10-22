"""User-owned PNG packs, independent of the immutable private artwork baseline."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image

from codex_pet.animation import AnimationCatalog


class AssetPackError(ValueError):
    """An actionable pack problem suitable for the first-run dialog."""


ACTION_ROWS = {
    "idle": 0, "running-right": 1, "running-left": 2, "waving": 3,
    "jumping": 4, "failed": 5, "waiting": 6, "running": 7, "review": 8,
    "yawn": 11, "groom": 12, "thinking": 13,
}
REQUIRED_ACTIONS = tuple(ACTION_ROWS)[:9]


@dataclass(frozen=True)
class AssetPack:
    manifest: Path
    catalog: AnimationCatalog
    frames: dict[tuple[int, int], Path]
    size: tuple[int, int]


def select_asset_pack(base: Path, resource_base: Path, explicit: Path | None = None) -> Path | None:
    """Private installs keep their baseline unless the user supplies a pack."""
    candidate = explicit or base / "user-assets" / "pet" / "pack.json"
    if candidate.is_file():
        return candidate
    if explicit is None and (resource_base / "assets/codex_pet/source-manifest.json").is_file():
        return None
    raise AssetPackError(
        f"缺少自备美术资产：{candidate}\n"
        "开源版不包含角色图片。请准备自己有权使用的透明 PNG，运行\n"
        "python tools/asset_pack.py init --image <图片路径>\n"
        "或将已有素材包放入 user-assets/pet/（包含 pack.json），然后重新启动。"
    )


def import_single_image(source: Path, destination: Path) -> Path:
    """Create a minimal pack without changing the source or overwriting a pack."""
    destination = destination.resolve()
    if (destination / "pack.json").exists() or (destination / "idle.png").exists():
        raise AssetPackError("目标目录已有 pack.json 或 idle.png，请保留原包并选择新目录")
    data = {"schema_version": 1, "animations": {
        "idle": {"frames": ["idle.png"], "durations_ms": [1500]}}}
    try:
        with tempfile.TemporaryDirectory(prefix="pet-pack-") as staging:
            staged = Path(staging)
            shutil.copyfile(source, staged / "idle.png")
            (staged / "pack.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
            load_asset_pack(staged / "pack.json")
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / "idle.png").open("xb") as output:
                output.write((staged / "idle.png").read_bytes())
            try:
                with (destination / "pack.json").open("x", encoding="utf-8") as output:
                    output.write(json.dumps(data, indent=2))
            except OSError:
                (destination / "idle.png").unlink()
                raise
    except OSError as exc:
        raise AssetPackError(f"无法创建素材包：{exc}") from exc
    return destination / "pack.json"


def load_asset_pack(manifest: Path) -> AssetPack:
    """Validate every frame before rendering; never load paths outside the pack."""
    manifest = manifest.resolve()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise AssetPackError("pack.json 需要 schema_version: 1")
        animations = data.get("animations")
        if not isinstance(animations, dict) or "idle" not in animations:
            raise AssetPackError("pack.json 至少需要 animations.idle")
        unknown = set(animations) - set(ACTION_ROWS)
        if unknown:
            raise AssetPackError(f"未知动作名称：{', '.join(sorted(unknown))}")
        checked: dict[Path, tuple[int, int]] = {}
        size = None

        def frame_path(name: str) -> Path:
            nonlocal size
            if not isinstance(name, str) or not name or "\\" in name:
                raise AssetPackError("帧路径必须是素材包内的相对路径，使用 / 分隔")
            path = (manifest.parent / name).resolve()
            if Path(name).is_absolute() or not path.is_relative_to(manifest.parent):
                raise AssetPackError("帧路径不能超出素材包目录")
            if path.suffix.lower() != ".png":
                raise AssetPackError(f"帧必须是 PNG：{name}")
            if path not in checked:
                with Image.open(path) as image:
                    if image.format != "PNG" or image.mode not in ("RGBA", "LA", "P"):
                        raise AssetPackError(f"需要带透明通道的 PNG：{name}")
                    if image.mode == "P" and "transparency" not in image.info:
                        raise AssetPackError(f"PNG 缺少透明通道：{name}")
                    if min(image.size) < 1 or max(image.size) > 4096:
                        raise AssetPackError(f"PNG 每边尺寸必须在 1–4096 像素之间：{name}")
                    if size is None:
                        size = image.size
                    if image.size != size:
                        raise AssetPackError(f"所有帧必须保持相同画布尺寸 {size}：{name}")
                    image.verify()
                checked[path] = size
            return path

        frames = {}
        specs = {}
        for name, row in ACTION_ROWS.items():
            if name not in animations and name not in REQUIRED_ACTIONS:
                continue
            spec = animations.get(name, animations["idle"])
            if not isinstance(spec, dict):
                raise AssetPackError(f"动作 {name} 必须是对象")
            names = spec.get("frames")
            if not isinstance(names, list) or not 1 <= len(names) <= 256:
                raise AssetPackError(f"动作 {name} 需要 1–256 个 frames")
            durations = spec.get("durations_ms", [150] * len(names))
            if (not isinstance(durations, list) or len(durations) != len(names)
                    or any(type(d) not in (int, float) or not math.isfinite(d)
                           or not 16 <= d <= 60_000 for d in durations)):
                raise AssetPackError(f"动作 {name} 每帧需要 16–60000 毫秒的 durations_ms")
            for column, filename in enumerate(names):
                frames[row, column] = frame_path(filename)
            default_loop = name not in ("waving", "jumping", "yawn", "groom", "thinking")
            loop = spec.get("loop", default_loop) if name in animations else default_loop
            if type(loop) is not bool:
                raise AssetPackError(f"动作 {name} 的 loop 必须为 true 或 false")
            specs[name] = {"row": row, "columns": list(range(len(names))),
                           "durations_ms": durations, "loop_default": loop,
                           "on_finish": "underlying_state"}
        look = data.get("look")
        if look is not None and (not isinstance(look, list) or len(look) != 16):
            raise AssetPackError("look 若提供，必须包含从正上方顺时针排列的 16 张 PNG")
        poses = []
        for index in range(16):
            row, column = 9 + index // 8, index % 8
            frames[row, column] = frame_path(look[index]) if look else frames[0, 0]
            poses.append({"index": index, "angle_degrees": index * 22.5,
                          "row": row, "column": column})
        catalog = AnimationCatalog({"custom_asset_pack": True, "cell_size_px": list(size),
                                    "animations": specs, "look": {"poses": poses}})
        return AssetPack(manifest, catalog, frames, size)
    except AssetPackError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AssetPackError(f"无法读取素材包 {manifest.name}：{exc}") from exc
