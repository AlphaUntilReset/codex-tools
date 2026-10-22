"""素材校验、图集裁切与运行帧缓存。

设计约束（见 施工方案.md 4.1/4.4）：
- 裁切坐标一律整数，帧保持完整 768×832 画布，不按人物外接框重裁。
- 运行帧必须与原图对应格 RGBA 完全一致，只允许无损 PNG 缓存。
- 不长期保留整张图集；构建缓存后立即释放。
"""

from __future__ import annotations

import hashlib
import json
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # 6144×9152 已知安全来源


@dataclass(frozen=True)
class AssetPaths:
    """素材与派生缓存的位置。"""

    webp: Path
    manifest: Path
    animations: Path
    cache_dir: Path

    @classmethod
    def default(cls, base: Path, cache_dir: Path | None = None) -> "AssetPaths":
        """base 为包含 assets/codex_pet 的根目录。"""
        codex_pet = base / "assets" / "codex_pet"
        return cls(
            webp=codex_pet / "spritesheet-hd.webp",
            manifest=codex_pet / "source-manifest.json",
            animations=codex_pet / "animations.reference.json",
            cache_dir=cache_dir or base / "build" / "frames",
        )


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def occupied_cells(manifest: dict) -> list[tuple[int, int]]:
    """按 manifest 的每行非空数量推导非空格；每行非空格为其前缀列。"""
    cells: list[tuple[int, int]] = []
    for row, count in enumerate(manifest["nonempty_cells_per_row"]):
        for col in range(count):
            cells.append((row, col))
    if len(cells) != manifest["nonempty_cells_total"]:
        raise ValueError(
            f"manifest 非空格数不一致: 每行合计 {len(cells)} != {manifest['nonempty_cells_total']}"
        )
    return cells


def crop_cell(atlas: Image.Image, row: int, col: int, cell: tuple[int, int]) -> Image.Image:
    """从图集裁出 [col*W, row*H, (col+1)*W, (row+1)*H)，保持整格画布。"""
    w, h = cell
    box = (col * w, row * h, (col + 1) * w, (row + 1) * h)
    return atlas.crop(box)


def verify_source(paths: AssetPaths) -> dict:
    """核对文件哈希与尺寸，返回核对结果；不一致时抛异常。"""
    manifest = load_json(paths.manifest)
    expect_sha = manifest["sha256"]
    expect_bytes = manifest["bytes"]
    actual_bytes = paths.webp.stat().st_size
    if actual_bytes != expect_bytes:
        raise ValueError(f"图集字节数不符: {actual_bytes} != {expect_bytes}")
    actual_sha = sha256_of(paths.webp)
    if actual_sha != expect_sha:
        raise ValueError(f"图集 SHA-256 不符: {actual_sha} != {expect_sha}")
    return {
        "sha256": actual_sha,
        "bytes": actual_bytes,
        "atlas_size_px": tuple(manifest["atlas_size_px"]),
        "grid": (manifest["grid_columns"], manifest["grid_rows"]),
        "cell_size_px": tuple(manifest["cell_size_px"]),
    }


CACHE_INDEX_VERSION = 1


def build_frame_cache(paths: AssetPaths, verify_sha: bool = True) -> dict:
    """解码图集一次，裁出全部非空格存为无损 PNG，并逐格验证 RGBA 一致。

    返回报告 dict；缓存索引写在 cache_dir/index.json。
    """
    info = load_json(paths.manifest)
    if verify_sha:
        verify_source(paths)
    cell = tuple(info["cell_size_px"])
    cells = occupied_cells(info)

    paths.cache_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "index_version": CACHE_INDEX_VERSION,
        "source_sha256": info["sha256"],
        "cell_size_px": list(cell),
        "cells": {},
        "empty_cells_all_transparent": True,
    }
    empty_cells = [
        (r, c)
        for r in range(info["grid_rows"])
        for c in range(info["grid_columns"])
        if (r, c) not in set(cells)
    ]

    atlas = Image.open(paths.webp)
    if atlas.mode != "RGBA":
        raise ValueError(f"图集应为 RGBA，实际 {atlas.mode}")
    if atlas.size != tuple(info["atlas_size_px"]):
        raise ValueError(f"图集尺寸不符: {atlas.size} != {tuple(info['atlas_size_px'])}")

    try:
        for row, col in empty_cells:
            tile = crop_cell(atlas, row, col, cell)
            if tile.getextrema()[3][1] != 0:  # alpha 通道最大值
                report["empty_cells_all_transparent"] = False
                raise ValueError(f"声称为空的格 ({row},{col}) 含非透明像素")
        for row, col in cells:
            tile = crop_cell(atlas, row, col, cell)
            if tile.size != cell:
                raise ValueError(f"格 ({row},{col}) 尺寸 {tile.size} != {cell}")
            out = paths.cache_dir / f"r{row:02d}_c{col:02d}.png"
            tile.save(out, format="PNG", compress_level=6)
            # 重新解码并比较逐像素 RGBA
            reloaded = Image.open(out)
            if reloaded.mode != "RGBA":
                reloaded = reloaded.convert("RGBA")
            if reloaded.tobytes() != tile.tobytes():
                raise ValueError(f"格 ({row},{col}) PNG 往返后像素不一致")
            alpha = tile.getchannel("A")
            nonzero = sum(1 for v in alpha.getdata() if v)
            report["cells"][f"{row},{col}"] = {
                "png_bytes": out.stat().st_size,
                "rgba_md5": hashlib.md5(tile.tobytes()).hexdigest(),
                "opaque_px": nonzero,
            }
    finally:
        atlas.close()

    with open(paths.cache_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def _png_chunks_intact(png: Path) -> bool:
    """校验 PNG 每个块的 CRC32（哈希级开销，不做像素解码）。

    字节数相同的就地损坏（如杀软部分隔离）骗过 png_bytes 大小检查，
    会让启动期的帧加载直接失败；CRC 校验能在新鲜度阶段识别并触发重建。
    """
    data = png.read_bytes()
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    pos = 8
    while pos + 12 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        end = pos + 12 + length
        if end > len(data):
            return False
        chunk = data[pos + 4 : end - 4]
        if zlib.crc32(chunk) != int.from_bytes(data[end - 4 : end], "big"):
            return False
        if chunk[:4] == b"IEND":
            return True
        pos = end
    return False


def cache_is_fresh(paths: AssetPaths) -> bool:
    """缓存与当前素材哈希、格数、文件完整性一致时返回 True。"""
    index_path = paths.cache_dir / "index.json"
    if not index_path.exists():
        return False
    try:
        index = load_json(index_path)
    except (json.JSONDecodeError, OSError):
        return False
    if index.get("index_version") != CACHE_INDEX_VERSION:
        return False
    manifest = load_json(paths.manifest)
    if index.get("source_sha256") != manifest["sha256"]:
        return False
    if index.get("cell_size_px") != list(manifest["cell_size_px"]):
        return False
    cells = index.get("cells")
    if not isinstance(cells, dict) or len(cells) != manifest["nonempty_cells_total"]:
        return False
    for key, meta in cells.items():
        png = paths.cache_dir / f"r{int(key.split(',')[0]):02d}_c{int(key.split(',')[1]):02d}.png"
        if not png.exists() or png.stat().st_size != meta["png_bytes"]:
            return False
        if not _png_chunks_intact(png):
            return False
    return True


class FrameStore:
    """按行动画行加载 PNG 帧的 LRU 存储。

    只保留少量最近使用的行，避免整本图集常驻内存（4.4）。
    """

    def __init__(self, cache_dir: Path, max_rows: int = 4):
        self._dir = Path(cache_dir)
        self._max_rows = max_rows
        self._rows: OrderedDict[int, dict[tuple[int, int], Image.Image]] = OrderedDict()

    def _load_row(self, row: int) -> dict[tuple[int, int], Image.Image]:
        frames: dict[tuple[int, int], Image.Image] = {}
        for png in sorted(self._dir.glob(f"r{row:02d}_c*.png")):
            parts = png.stem.split("_")
            col = int(parts[1][1:])
            frames[(row, col)] = Image.open(png).convert("RGBA")
        if not frames:
            raise FileNotFoundError(f"缓存缺少第 {row} 行帧: {self._dir}")
        return frames

    def row_frames(self, row: int) -> dict[tuple[int, int], Image.Image]:
        if row in self._rows:
            self._rows.move_to_end(row)
            return self._rows[row]
        frames = self._load_row(row)
        self._rows[row] = frames
        while len(self._rows) > self._max_rows:
            self._rows.popitem(last=False)
        return frames

    def frame(self, row: int, col: int) -> Image.Image:
        return self.row_frames(row)[(row, col)]

    def close(self) -> None:
        self._rows.clear()


def iter_animation_cells(animations: dict) -> Iterator[tuple[str, int, int]]:
    """产出 (动画名, 行, 列)；不含朝向姿势与额外静态格。"""
    for name, spec in animations["animations"].items():
        row = spec["row"]
        for col in spec["columns"]:
            yield name, row, col
