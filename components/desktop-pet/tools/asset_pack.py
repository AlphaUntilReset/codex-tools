"""Create or validate a user-owned PNG pack; no private artwork is read."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from codex_pet.custom_assets import AssetPackError, import_single_image, load_asset_pack


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="自备桌宠 PNG 素材包")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="用一张 PNG 创建最小素材包，不覆盖现有文件")
    init.add_argument("--image", type=Path, required=True)
    init.add_argument("--output", type=Path, default=ROOT / "user-assets/pet")
    validate = commands.add_parser("validate", help="校验全部帧、尺寸、时间与目录边界")
    validate.add_argument("manifest", nargs="?", type=Path, default=ROOT / "user-assets/pet/pack.json")
    args = parser.parse_args(argv)
    try:
        manifest = (import_single_image(args.image, args.output)
                    if args.command == "init" else args.manifest)
        pack = load_asset_pack(manifest)
    except AssetPackError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"素材包有效：{manifest} ({pack.size[0]}×{pack.size[1]}, "
          f"{len(set(pack.frames.values()))} 张图片, {len(pack.catalog.animations)} 个动作)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
