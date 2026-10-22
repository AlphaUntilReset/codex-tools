"""帧渲染：从无损 PNG 缓存加载，按目标物理尺寸生成缓存，单次受控缩放。

缩放链路（施工方案 4.2/4.3）：
- 源帧 768×832（straight RGBA）→ 转 ARGB32_Premultiplied（唯一一次预乘转换）
→ SmoothTransformation 缩放到目标物理尺寸 → QPixmap(dpr) 1:1 绘制。
- 缓存键 = (行, 列, 物理宽, 物理高, DPR)；从不基于已缩小的图二次缩放。
"""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import logging
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QPainter

log = logging.getLogger("codex_pet.renderer")


class FrameRenderer:
    """全分辨率帧 LRU + 目标尺寸 QPixmap LRU，按内存预算淘汰。"""

    def __init__(self, cache_dir: Path, full_budget_bytes: int = 96 << 20, scaled_budget_bytes: int = 48 << 20,
                 *, look_dir: Path | None = None, extra_frames: dict | None = None,
                 registration_file: Path | None = None, motion_dir: Path | None = None,
                 baseline_profile: Path | None = None, baseline_display_cache: Path | None = None,
                 consistent_eye_gaze: bool = False):
        if baseline_profile is not None and (extra_frames or registration_file is not None or motion_dir is not None):
            raise ValueError('0.4.7 baseline cannot be mixed with reconstructed animation packs')
        self._dir = Path(cache_dir)
        if consistent_eye_gaze and baseline_profile is None:
            raise ValueError('consistent eye gaze requires the verified original front baseline')
        self._consistent_eye_gaze=consistent_eye_gaze
        self._extra_frames = dict(extra_frames or {})
        self.available_motion_groups = None
        if motion_dir is not None:
            from codex_pet.motion_pack import load_motion_pack
            overrides,self.available_motion_groups=load_motion_pack(Path(motion_dir),self._dir)
            self._extra_frames.update(overrides)
        self._color_profiles = {}
        self._look_dir = self._validated_look_dir(look_dir)
        self._offsets = self._validated_offsets(registration_file)
        self._baseline_specs = None
        self.baseline_available_groups = None
        if baseline_profile is not None:
            self._baseline_specs, self.baseline_available_groups = self._validated_baseline(baseline_profile)
        self._display_cache = {}
        if baseline_display_cache is not None:
            if baseline_profile is None:
                raise ValueError('derived display cache requires the reviewed baseline profile')
            self._display_cache = self._validated_display_cache(Path(baseline_display_cache))
        self._full: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._scaled: OrderedDict[tuple, QPixmap] = OrderedDict()
        self._full_budget = full_budget_bytes
        self._scaled_budget = scaled_budget_bytes
        self._full_bytes = 0
        self._scaled_bytes = 0
        self.stats = {"full_hits": 0, "full_misses": 0, "scaled_hits": 0, "scaled_misses": 0}
        if consistent_eye_gaze:
            log.info('启用同一正面脸眼神跟随: 16 方向，内眼纹理位移最多 3×2 源像素')

    # ---- 全分辨率帧 ----

    def _validated_display_cache(self, directory: Path):
        from codex_pet.baseline_047 import REVIEWED_CACHE_INDEX_SHA, REVIEWED_PROFILE_SHA
        paths={}
        try:
            raw=(directory/'index.json').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=REVIEWED_CACHE_INDEX_SHA:
                raise ValueError('derived display cache index changed')
            index=json.loads(raw)
            if index['schema_version']!=1 or index['profile_sha256']!=REVIEWED_PROFILE_SHA:
                raise ValueError('wrong display cache profile')
            for cell,s in index['frames'].items():
                key=tuple(map(int,cell.split(',')));r,c=key
                if s['file']!=f'r{r:02d}_c{c:02d}.png':raise ValueError('unsafe display cache path')
                p=directory/s['file']
                if key in self._baseline_specs:
                    try:valid=hashlib.sha256(p.read_bytes()).hexdigest()==s['sha256']
                    except OSError:valid=False
                    if valid:paths[key]=(p,s['sha256'])
            for group in tuple(self.baseline_available_groups):
                cells={k for k,s in self._baseline_specs.items() if s['group']==group}
                if not cells<=set(paths):
                    self.baseline_available_groups.remove(group)
                    self._baseline_specs={k:s for k,s in self._baseline_specs.items() if s['group']!=group}
                    paths={k:v for k,v in paths.items() if k not in cells}
                    log.warning('0.4.7 无损显示缓存损坏，动作静态降级: %s',group)
            log.info('启用 0.4.7 无损显示缓存: %d 帧',len(paths))
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            self._baseline_specs={};self.baseline_available_groups=set()
            log.warning('0.4.7 无损显示缓存不可用，使用原正面静态姿势')
            return {}
        return paths

    def _validated_baseline(self, file: Path):
        from codex_pet.baseline_047 import (ALGORITHM, MOVING_ROWS, MAX_OFFSET,
            MAX_DELTA, PALETTE_NAMES, regions_for_cell, REVIEWED_PROFILE_SHA)
        expected={(r,c) for r,count in ((0,6),(1,8),(2,8),(3,4),(4,5),(5,8),(6,6),(7,6),(8,6),(9,8),(10,8)) for c in range(count)}
        specs={};groups=set()
        try:
            content=Path(file).read_bytes()
            if hashlib.sha256(content).hexdigest()!=REVIEWED_PROFILE_SHA:
                raise ValueError('baseline display metadata changed since review')
            data=json.loads(content)
            if data['schema_version']!=1 or data['algorithm']!=ALGORITHM or data['cell_size_px']!=[768,832]:
                raise ValueError('invalid baseline profile header')
            parsed={tuple(map(int,k.split(','))):s for k,s in data['frames'].items()}
            if set(parsed)!=expected:
                raise ValueError('invalid baseline profile layout')
            row_groups=('idle','running-right','running-left','waving','jumping','failed','waiting','running','review','look','look')
            for key,s in parsed.items():
                r,c=key;offset=s['offset_px'];deltas=s['color_delta_rgb']
                if s['group']!=row_groups[r] or len(offset)!=2 or any(type(v) is not int or abs(v)>MAX_OFFSET for v in offset):
                    raise ValueError('invalid baseline root')
                if r in MOVING_ROWS and offset!=[0,0]:
                    raise ValueError('gait/jump root must remain original')
                if tuple(s['palette_regions_y'])!=regions_for_cell(r,c) or set(deltas)-PALETTE_NAMES:
                    raise ValueError('unreviewed baseline palette regions')
                if any(len(d)!=3 or any(type(v) is not int or abs(v)>MAX_DELTA for v in d) for d in deltas.values()):
                    raise ValueError('invalid baseline palette delta')
                if r==0 and any(any(d) for d in deltas.values()):
                    raise ValueError('idle palette must remain original')
                # Source selection is the 0.4.7 path, checked BEFORE rendering.
                p=self._png_path(r,c)
                try:
                    valid=hashlib.sha256(p.read_bytes()).hexdigest()==s['source_sha256']
                except OSError:
                    valid=False
                if valid:specs[key]=s
            for group in set(row_groups):
                cells={k for k,s in parsed.items() if s['group']==group}
                if cells<=set(specs):groups.add(group)
                else:
                    specs={k:s for k,s in specs.items() if s['group']!=group}
                    log.warning('0.4.7 原素材校验失败，动作静态降级: %s',group)
            log.info('启用 0.4.7 原素材显示修复: %d 组 / %d 帧',len(groups),len(specs))
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            log.warning('0.4.7 显示校准不可用，使用原正面静态姿势')
            specs={};groups=set()
        return specs,groups

    def _validated_offsets(self, file: Path | None) -> dict:
        offsets = {}
        if file is None:
            return offsets
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            for cell, spec in data["frames"].items():
                row, col = map(int, cell.split(","))
                dx, dy = spec["offset_px"]
                if row in (1, 2, 4) or any(type(v) is not int or abs(v) > 32 for v in (dx,dy)):
                    raise ValueError("动作位移校准超出范围")
                source = self._png_path(row, col)
                try:
                    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
                except OSError:
                    continue # Optional group fallback must not disable other frame calibrations.
                if source_hash == spec["source_sha256"]:
                    offsets[row,col] = (dx,dy)
                    profile=spec.get("color_delta_rgb", {})
                    if row>=11 and profile:
                        limit=32 if row==13 else 24
                        if any(len(delta)!=3 or any(type(v) is not int or abs(v)>limit for v in delta) for delta in profile.values()):
                            raise ValueError("动作配色校准超出范围")
                        self._color_profiles[row,col]=profile
            log.info("启用动作站位校准: %d 格", len(offsets))
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("动作站位校准不可用，使用原始坐标")
            self._color_profiles.clear()
            return {}
        return offsets

    @staticmethod
    def _validated_look_dir(directory: Path | None) -> Path | None:
        """新版朝向为独立资源；16 格整体核验通过才启用，原图集及缓存保持只读。"""
        if directory is None or not directory.exists():
            return None
        try:
            metadata = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                raise ValueError("朝向资源清单结构不符")
            expected = {f"r{row:02d}_c{col:02d}.png" for row in (9, 10) for col in range(8)}
            if metadata.get("cell_size_px") != [768, 832] or set(metadata["frames"]) != expected:
                raise ValueError("朝向资源布局不符")
            for name in sorted(expected):
                file = directory / name
                if hashlib.sha256(file.read_bytes()).hexdigest() != metadata["frames"][name]["sha256"]:
                    raise ValueError("朝向资源哈希不符")
                image = QImage(str(file))
                if image.isNull() or image.size() != QSize(768, 832) or not image.hasAlphaChannel():
                    raise ValueError("朝向资源尺寸或透明通道不符")
            log.info("启用统一画风朝向资源: 16 格")
            return directory
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("新版朝向资源不可用，回退原始朝向")
            return None

    def _png_path(self, row: int, col: int) -> Path:
        if (row, col) in self._extra_frames:
            return self._extra_frames[row, col]
        if self._look_dir is not None and row in (9, 10) and 0 <= col < 8:
            return self._look_dir / f"r{row:02d}_c{col:02d}.png"
        return self._dir / f"r{row:02d}_c{col:02d}.png"

    def full_frame(self, row: int, col: int) -> QImage:
        """768×832 预乘格式全分辨率帧（供缩放与命中测试）。"""
        key = (row, col)
        img = self._full.get(key)
        if img is not None:
            self.stats["full_hits"] += 1
            self._full.move_to_end(key)
            return img
        if self._consistent_eye_gaze and row in (9,10) and 0<=col<8:
            from codex_pet.eye_gaze import render_gaze
            # No generated direction image is read here. Every pose starts
            # from the SAME verified, registered r00_c00 runtime frame.
            front=self.full_frame(0,0)
            # Invalid baseline/cache metadata retains the original static
            # fallback contract; do not animate a degraded reference.
            img=(render_gaze(front,(row-9)*8+col)
                 if (0,0) in self._baseline_specs else front)
            return self._remember_full_frame(key,img)
        path = self._png_path(row, col)
        baseline_spec = None
        if self._baseline_specs is not None:
            baseline_spec=self._baseline_specs.get(key)
            if baseline_spec is None:
                path=self._dir/'r00_c00.png'
                from codex_pet.baseline_047 import REFERENCE_PNG_SHA
                if hashlib.sha256(path.read_bytes()).hexdigest()!=REFERENCE_PNG_SHA:
                    raise FileNotFoundError(f'0.4.7 正面母版缺失或改变: {path}')
            elif hashlib.sha256(path.read_bytes()).hexdigest()!=baseline_spec['source_sha256']:
                # Detect runtime tampering as a frame enters the LRU; do not
                # apply a verified profile to newly replaced artwork.
                raise FileNotFoundError(f'0.4.7 原素材运行期发生变化: {path}')
        display_entry=self._display_cache.get(key)
        if display_entry is not None:
            display_path,display_sha=display_entry
            if hashlib.sha256(display_path.read_bytes()).hexdigest()!=display_sha:
                raise FileNotFoundError(f'0.4.7 显示缓存运行期发生变化: {display_path}')
            path=display_path
        source=QImage(str(path))
        if source.isNull():
            raise FileNotFoundError(f"帧缓存缺失或损坏: {path}")
        if key in self._color_profiles:
            from codex_pet.color_profile import apply_profile
            source=apply_profile(source,self._color_profiles[key])
        if baseline_spec is not None and display_entry is None:
            from codex_pet.baseline_047 import correct_palette
            source=correct_palette(source,baseline_spec['color_delta_rgb'],
                                   baseline_spec['palette_regions_y'])
        img = source.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        dx, dy = self._offsets.get(key, (0,0))
        if baseline_spec is not None and display_entry is None:
            dx,dy=baseline_spec['offset_px']
        if dx or dy:
            canvas = QImage(img.size(), QImage.Format_ARGB32_Premultiplied)
            canvas.fill(Qt.transparent)
            painter = QPainter(canvas)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.drawImage(dx,dy,img)
            painter.end()
            img = canvas
        return self._remember_full_frame(key,img)

    def _remember_full_frame(self,key,img):
        self.stats["full_misses"] += 1
        self._full[key] = img
        self._full_bytes += img.sizeInBytes()
        while self._full_bytes > self._full_budget and len(self._full) > 1:
            _, evicted = self._full.popitem(last=False)
            self._full_bytes -= evicted.sizeInBytes()
        return img

    def alpha_at(self, row: int, col: int, x: int, y: int) -> int:
        """源帧坐标处的 alpha（0–255）；预乘不改变 alpha 通道。"""
        img = self.full_frame(row, col)
        if not (0 <= x < img.width() and 0 <= y < img.height()):
            return 0
        return (img.pixel(x, y) >> 24) & 0xFF

    # ---- 目标尺寸缓存 ----

    def pixmap(self, row: int, col: int, phys_size: QSize, dpr: float) -> QPixmap:
        key = (row, col, phys_size.width(), phys_size.height(), round(dpr, 4))
        pm = self._scaled.get(key)
        if pm is not None:
            self.stats["scaled_hits"] += 1
            self._scaled.move_to_end(key)
            return pm
        source = self.full_frame(row, col)
        scaled = (
            source.scaled(
                phys_size.width(),
                phys_size.height(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,
            )
            if phys_size != source.size()
            else source.copy()
        )
        pm = QPixmap.fromImage(scaled)
        pm.setDevicePixelRatio(dpr)
        self.stats["scaled_misses"] += 1
        self._scaled[key] = pm
        self._scaled_bytes += pm.size().width() * pm.size().height() * 4
        while self._scaled_bytes > self._scaled_budget and len(self._scaled) > 1:
            _, evicted = self._scaled.popitem(last=False)
            self._scaled_bytes -= evicted.size().width() * evicted.size().height() * 4
        return pm

    def clear_scaled(self) -> None:
        self._scaled.clear()
        self._scaled_bytes = 0

    def memory_report(self) -> dict:
        return {
            "full_frames": len(self._full),
            "full_mib": round(self._full_bytes / (1 << 20), 1),
            "scaled_frames": len(self._scaled),
            "scaled_mib": round(self._scaled_bytes / (1 << 20), 1),
            **self.stats,
        }
