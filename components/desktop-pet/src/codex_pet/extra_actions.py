"""Validated optional animation groups shared by the catalog and renderer."""
from pathlib import Path
import hashlib
import json
import logging
from PIL import Image
from codex_pet.animation import AnimationDef

EXTRA_ROWS = {"yawn": 12, "groom": 13, "thinking": 14}

def _load_groups(directory, catalog, paths, names, *, extended_groom=False):
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for name in names:
        row=EXTRA_ROWS[name]
        try:
            spec = manifest["actions"][name]
            durations = spec["durations_ms"]
            frames = spec["frames"]
            count=len(frames)
            valid_count=8 <= count <= 24 if extended_groom and name=='groom' else count==8
            if spec["row"] != row or not valid_count or len(durations) != count:
                raise ValueError("invalid layout")
            if any(type(d) not in (int, float) or not 0 < d <= 10000 for d in durations):
                raise ValueError("invalid duration")
            group = {}
            for column, item in enumerate(frames):
                path = directory / f"r{row:02d}_c{column:02d}.png"
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("invalid hash")
                with Image.open(path) as image:
                    if image.size != (768, 832) or image.mode != "RGBA":
                        raise ValueError("invalid image")
                    image.load()
                group[row, column] = path
            catalog.animations[name] = AnimationDef(name, row, tuple(range(count)),
                                                   tuple(durations), False, "underlying_state")
            for key in list(paths):
                if key[0]==row:
                    del paths[key]
            paths.update(group)
        except (OSError, ValueError, KeyError, TypeError):
            logging.getLogger("codex_pet.actions").warning("扩展动作不可用: %s", name)

def load_extra_actions(directory: Path, catalog, *, groom_directory: Path | None = None,
                       strict_groom: bool = False,
                       disabled_actions: tuple[str, ...] = ()) -> dict[tuple[int, int], Path]:
    """Load groups; production strict mode never falls back to rejected groom art."""
    paths = {}
    names=tuple(n for n in EXTRA_ROWS if n not in disabled_actions and (not strict_groom or n!='groom'))
    _load_groups(directory,catalog,paths,names)
    if groom_directory is not None and 'groom' not in disabled_actions:
        _load_groups(groom_directory,catalog,paths,('groom',),extended_groom=True)
    return paths
