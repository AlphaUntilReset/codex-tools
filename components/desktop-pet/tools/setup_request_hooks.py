"""Prepare/install the optional notice hooks; never grants Codex hook trust.

Default is preview only. --install merges into user hooks after making a backup.
--remove removes only entries with this exact observer command.
"""
import argparse
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def definition(executable):
    path = str(executable.resolve()).replace("'", "''")
    command = 'powershell.exe -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -Command "& ' + "'" + path + "'\""
    handler = {"type": "command", "command": command, "commandWindows": command, "timeout": 3}
    return {"description": "桌宠宠物：仅记录审批/输入请求的元数据，不作许可决定。",
            "hooks": {
                "PermissionRequest": [{"hooks": [copy.deepcopy(handler)]}],
                "PreToolUse": [{"matcher": "^(functions\\.)?request_user_input$", "hooks": [copy.deepcopy(handler)]}],
            }}


def merge_hooks(existing, proposed, remove=False):
    result = copy.deepcopy(existing)
    if not isinstance(result, dict) or not isinstance(result.get("hooks", {}), dict):
        raise ValueError("Existing hooks configuration is not a supported object")
    hooks = result.setdefault("hooks", {})
    for event, groups in proposed["hooks"].items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise ValueError("Existing hook groups are not a list")
        for group in groups:
            if remove:
                current[:] = [g for g in current if g != group]
            elif group not in current:
                current.append(copy.deepcopy(group))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--executable", type=Path, default=ROOT / "dist/CodexToolsPet/hooks/CodexToolsHook.exe")
    args = parser.parse_args()
    proposed = definition(args.executable)
    preview = ROOT / "build/codex_pet-hooks.preview.json"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.install and not args.remove:
        print("Preview only:", preview)
        return 0
    if args.install and args.remove:
        parser.error("Choose install or remove")
    if args.install and not args.executable.is_file():
        parser.error("Build the observer executable first")
    target = args.home.resolve() / "hooks.json"
    old_bytes = target.read_bytes() if target.exists() else None
    existing = json.loads(old_bytes.decode("utf-8")) if old_bytes is not None else {}
    merged = merge_hooks(existing, proposed, remove=args.remove)
    if merged == existing:
        print("No change")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    if old_bytes is not None:
        backup = target.with_name("hooks.before-codex_pet-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".json")
        backup.write_bytes(old_bytes)
    # Re-read before replacing so another agent's edits are never overwritten.
    if (target.read_bytes() if target.exists() else None) != old_bytes:
        raise RuntimeError("hooks.json changed during setup; no replacement performed")
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=".codex_pet-hooks-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(merged, stream, ensure_ascii=False, indent=2)
    os.replace(temp, target)
    print("Removed" if args.remove else "Installed, still requires the official /hooks trust review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
